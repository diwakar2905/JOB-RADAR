"""Job Radar autonomous background pipeline runner."""

import argparse
import atexit
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from radar.db import (
    get_source_cursor,
    init_db,
    insert_match,
    insert_opening,
    opening_exists_by_hash,
    record_run_finish,
    record_run_start,
    set_source_cursor,
)
from radar.filter import apply_filters
from radar.link_checker import check_apply_link
from radar.normalize import compute_dedupe_hash, infer_seniority, normalize_location
from radar.notifier import notify
from radar.profile import load_profile
from radar.research import CompanyResearcher
from radar.score import FitScorer
from radar.sources import (
    ATSSource,
    HackerNewsHiringSource,
    TavilySearchSource,
    YCStartupSource,
)

load_dotenv()

LOCK_FILE = Path("job_radar.lock")


def acquire_lock() -> bool:
    """Acquires lock file to prevent overlapping runs."""
    if LOCK_FILE.exists():
        # Check if stale (older than 2 hours)
        try:
            mtime = LOCK_FILE.stat().st_mtime
            if (datetime.now().timestamp() - mtime) > 7200:
                LOCK_FILE.unlink(missing_ok=True)
            else:
                return False
        except Exception:
            return False

    try:
        LOCK_FILE.write_text(str(os.getpid()))
        atexit.register(release_lock)
        return True
    except Exception:
        return False


def release_lock():
    try:
        if LOCK_FILE.exists():
            LOCK_FILE.unlink()
    except Exception:
        pass


def load_config(config_path: str = "config.yaml") -> dict[str, Any]:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_config(config: dict[str, Any], config_path: str = "config.yaml") -> None:
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, sort_keys=False, allow_unicode=True)


def execute_pipeline(
    config_path: str = "config.yaml",
    profile_path: str = "profile.json",
    dry_run: bool = False,
    single_source: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Runs the complete discovery, filtering, research, and scoring pipeline."""
    config = load_config(config_path)
    profile = load_profile(profile_path)
    init_db()

    max_matches = limit or config.get("max_new_matches_per_run", 50)
    alert_threshold = config.get("min_fit_score_alert", 80)

    # Initialize sources
    sources_to_run = []
    ats_source = ATSSource(watchlist_config=config.get("watchlist_ats"))
    hn_source = HackerNewsHiringSource()
    tavily_source = TavilySearchSource(config=config)
    yc_source = YCStartupSource()

    all_sources = [ats_source, hn_source, tavily_source, yc_source]
    if single_source:
        sources_to_run = [s for s in all_sources if s.name.lower() == single_source.lower()]
    else:
        sources_to_run = all_sources

    source_names = [s.name for s in sources_to_run]
    run_id = record_run_start(source_names)
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Started Job Radar run #{run_id} for sources: {source_names}")

    researcher = CompanyResearcher()
    scorer = FitScorer()

    new_openings_count = 0
    errors: list[str] = []
    top_matches_found = []

    for source in sources_to_run:
        print(f"\n--- Checking source: {source.name} ---")
        cursor = get_source_cursor(source.name)
        try:
            raw_openings, new_cursor = source.discover(cursor=cursor)
            if new_cursor and not dry_run:
                set_source_cursor(source.name, new_cursor)

            print(f"Source '{source.name}' returned {len(raw_openings)} raw items.")
        except Exception as e:
            err_msg = f"Error querying source {source.name}: {e}"
            print(err_msg)
            errors.append(err_msg)
            continue

        for item in raw_openings:
            if new_openings_count >= max_matches:
                print(f"Reached max limit of {max_matches} new matches for this run.")
                break

            # 1. Normalization & Deduplication Hash
            norm_loc, is_remote = normalize_location(item.location, item.remote)
            dedupe_hash = compute_dedupe_hash(item.company_domain, item.title, norm_loc)

            if not dry_run and opening_exists_by_hash(dedupe_hash):
                continue  # Skip already known opening

            # 2. Filter dealbreakers and role targets
            item.location = norm_loc
            item.remote = is_remote
            filter_res = apply_filters(item, config)
            if not filter_res.passed:
                continue

            # 3. Verify apply link health
            link_healthy = check_apply_link(item.apply_url)
            if not link_healthy:
                print(f"[!] Warning: Dead or unreachable apply link for {item.company_name} - {item.title}: {item.apply_url}")

            # 4. Research company (14-day cached)
            company_info = researcher.research_company(item.company_name, item.company_domain)

            # 5. Score Fit (0-100) with citation URLs
            inferred_sen = item.seniority or infer_seniority(item.title, item.description)
            opening_dict = {
                "title": item.title,
                "location": item.location,
                "remote": item.remote,
                "apply_url": item.apply_url,
                "description": item.description,
                "seniority": inferred_sen,
            }
            score_res = scorer.score_fit(profile, config, opening_dict, company_info)

            print(f"  + [{score_res.score}/100] {item.company_name} - {item.title} ({norm_loc})")
            print(f"    Reason: {score_res.reason}")

            if dry_run:
                new_openings_count += 1
                continue

            # 6. Store in SQLite
            company_id = company_info["id"]
            opening_id = insert_opening(
                company_id=company_id,
                title=item.title,
                seniority=inferred_sen,
                location=norm_loc,
                remote=is_remote,
                apply_url=item.apply_url,
                source=item.source,
                posted_at=item.posted_at,
                dedupe_hash=dedupe_hash,
                status_head_ok=link_healthy,
            )

            insert_match(
                opening_id=opening_id,
                score=score_res.score,
                reason=score_res.reason,
                sources=score_res.sources,
                status="new",
            )

            new_openings_count += 1
            if score_res.score >= alert_threshold:
                top_matches_found.append((item.company_name, item.title, score_res.score))

    record_run_finish(run_id, new_openings_count, errors)
    print(f"\nCompleted run #{run_id}: stored {new_openings_count} new matches ({len(errors)} errors).")

    # Send notification if top matches discovered
    if top_matches_found:
        best = top_matches_found[0]
        notify(
            title=f"Job Radar: {len(top_matches_found)} High-Fit Matches!",
            message=f"Top match: {best[0]} - {best[1]} (Score: {best[2]}/100)",
        )

    return {"run_id": run_id, "new_openings": new_openings_count, "errors": errors}


def main():
    parser = argparse.ArgumentParser(description="Job Radar Discovery Runner")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--profile", default="profile.json", help="Path to profile.json")
    parser.add_argument("--dry-run", action="store_true", help="Run without persisting to SQLite")
    parser.add_argument("--source", help="Run only specific source (ats, hn, tavily, yc)")
    parser.add_argument("--limit", type=int, help="Max new matches to discover")
    args = parser.parse_args()

    if not acquire_lock():
        print("Another Job Radar run is already in progress. Exiting.")
        sys.exit(0)

    try:
        execute_pipeline(
            config_path=args.config,
            profile_path=args.profile,
            dry_run=args.dry_run,
            single_source=args.source,
            limit=args.limit,
        )
    finally:
        release_lock()


if __name__ == "__main__":
    main()
