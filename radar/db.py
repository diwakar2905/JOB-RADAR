"""SQLite Database operations and schema management for Job Radar."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = Path("db.sqlite")


def get_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    """Initialize database tables with indexes."""
    with get_connection(db_path) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            domain TEXT UNIQUE NOT NULL,
            stage TEXT,
            funding TEXT,
            location TEXT,
            founders TEXT,
            summary TEXT,
            sources TEXT, -- JSON array of URLs
            researched_at TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS openings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            seniority TEXT,
            location TEXT,
            remote BOOLEAN DEFAULT 0,
            apply_url TEXT NOT NULL,
            source TEXT NOT NULL,
            posted_at TIMESTAMP,
            dedupe_hash TEXT UNIQUE NOT NULL,
            first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status_head_ok BOOLEAN DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opening_id INTEGER UNIQUE REFERENCES openings(id) ON DELETE CASCADE,
            score INTEGER NOT NULL,
            reason TEXT NOT NULL,
            sources TEXT, -- JSON array of URLs
            status TEXT DEFAULT 'new', -- new, saved, applied, skipped, interviewing, rejected
            feedback TEXT, -- good, bad, notes
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TIMESTAMP NOT NULL,
            finished_at TIMESTAMP,
            sources_checked TEXT, -- JSON array
            new_openings INTEGER DEFAULT 0,
            errors TEXT -- JSON array
        );

        CREATE TABLE IF NOT EXISTS source_cursors (
            source TEXT PRIMARY KEY,
            last_checked_at TIMESTAMP,
            cursor TEXT
        );

        CREATE TABLE IF NOT EXISTS api_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            cost_cents REAL DEFAULT 0.0,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_openings_hash ON openings(dedupe_hash);
        CREATE INDEX IF NOT EXISTS idx_matches_status ON matches(status);
        CREATE INDEX IF NOT EXISTS idx_matches_score ON matches(score DESC);
        CREATE INDEX IF NOT EXISTS idx_companies_domain ON companies(domain);
        """)
        conn.commit()


def get_or_create_company(
    name: str,
    domain: str,
    stage: str | None = None,
    funding: str | None = None,
    location: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Returns company id; creates company record if not found."""
    domain_clean = domain.strip().lower()
    with get_connection(db_path) as conn:
        cur = conn.execute("SELECT id FROM companies WHERE domain = ?", (domain_clean,))
        row = cur.fetchone()
        if row:
            return row["id"]

        cur = conn.execute(
            """INSERT INTO companies (name, domain, stage, funding, location)
               VALUES (?, ?, ?, ?, ?)""",
            (name.strip(), domain_clean, stage, funding, location),
        )
        conn.commit()
        return cur.lastrowid


def update_company_research(
    company_id: int,
    stage: str | None,
    funding: str | None,
    founders: str | None,
    summary: str | None,
    sources: list[str],
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    sources_json = json.dumps(sources)
    with get_connection(db_path) as conn:
        conn.execute(
            """UPDATE companies
               SET stage = coalesce(?, stage),
                   funding = coalesce(?, funding),
                   founders = coalesce(?, founders),
                   summary = coalesce(?, summary),
                   sources = ?,
                   researched_at = ?
               WHERE id = ?""",
            (stage, funding, founders, summary, sources_json, now, company_id),
        )
        conn.commit()


def get_company_by_id(company_id: int, db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any] | None:
    with get_connection(db_path) as conn:
        cur = conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,))
        row = cur.fetchone()
        if not row:
            return None
        res = dict(row)
        if res.get("sources"):
            try:
                res["sources"] = json.loads(res["sources"])
            except Exception:
                res["sources"] = []
        return res


def get_company_by_domain(domain: str, db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any] | None:
    with get_connection(db_path) as conn:
        cur = conn.execute("SELECT * FROM companies WHERE domain = ?", (domain.strip().lower(),))
        row = cur.fetchone()
        if not row:
            return None
        res = dict(row)
        if res.get("sources"):
            try:
                res["sources"] = json.loads(res["sources"])
            except Exception:
                res["sources"] = []
        return res


def get_companies_overview(stage: str | None = None, db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    """Researched companies with their opening/match counts, for the Companies page."""
    query = """
    SELECT
        c.id, c.name, c.domain, c.stage, c.funding, c.location, c.founders,
        c.summary, c.sources, c.researched_at,
        COUNT(DISTINCT o.id) AS opening_count,
        COUNT(DISTINCT m.id) AS match_count,
        MAX(m.score) AS best_score
    FROM companies c
    LEFT JOIN openings o ON o.company_id = c.id
    LEFT JOIN matches m ON m.opening_id = o.id
    """
    params: list[Any] = []
    if stage and stage != "all":
        query += " WHERE c.stage = ?"
        params.append(stage)
    query += " GROUP BY c.id ORDER BY best_score DESC, opening_count DESC, c.name ASC"

    with get_connection(db_path) as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            if d.get("sources"):
                try:
                    d["sources"] = json.loads(d["sources"])
                except Exception:
                    d["sources"] = []
            else:
                d["sources"] = []
            results.append(d)
        return results


def get_distinct_company_stages(db_path: Path = DEFAULT_DB_PATH) -> list[str]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT stage FROM companies WHERE stage IS NOT NULL AND stage != '' ORDER BY stage"
        ).fetchall()
        return [r["stage"] for r in rows]


def get_openings_for_company(company_id: int, db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    query = """
    SELECT o.id, o.title, o.location, o.remote, o.apply_url, o.source, o.posted_at,
           o.status_head_ok, m.score, m.status AS match_status
    FROM openings o
    LEFT JOIN matches m ON m.opening_id = o.id
    WHERE o.company_id = ?
    ORDER BY m.score DESC NULLS LAST, o.first_seen_at DESC
    """
    with get_connection(db_path) as conn:
        rows = conn.execute(query, (company_id,)).fetchall()
        return [dict(r) for r in rows]


def opening_exists_by_hash(dedupe_hash: str, db_path: Path = DEFAULT_DB_PATH) -> bool:
    with get_connection(db_path) as conn:
        cur = conn.execute("SELECT 1 FROM openings WHERE dedupe_hash = ?", (dedupe_hash,))
        return cur.fetchone() is not None


def insert_opening(
    company_id: int,
    title: str,
    seniority: str | None,
    location: str | None,
    remote: bool,
    apply_url: str,
    source: str,
    dedupe_hash: str,
    posted_at: str | None = None,
    status_head_ok: bool = True,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO openings
               (company_id, title, seniority, location, remote, apply_url, source, posted_at, dedupe_hash, status_head_ok)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                company_id,
                title,
                seniority,
                location,
                1 if remote else 0,
                apply_url,
                source,
                posted_at,
                dedupe_hash,
                1 if status_head_ok else 0,
            ),
        )
        conn.commit()
        return cur.lastrowid


def insert_match(
    opening_id: int,
    score: int,
    reason: str,
    sources: list[str],
    status: str = "new",
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    sources_json = json.dumps(sources)
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO matches (opening_id, score, reason, sources, status)
               VALUES (?, ?, ?, ?, ?)""",
            (opening_id, score, reason, sources_json, status),
        )
        conn.commit()
        return cur.lastrowid


def update_match_status(match_id: int, status: str, db_path: Path = DEFAULT_DB_PATH) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_connection(db_path) as conn:
        conn.execute("UPDATE matches SET status = ?, updated_at = ? WHERE id = ?", (status, now, match_id))
        conn.commit()


def update_match_feedback(match_id: int, feedback: str, db_path: Path = DEFAULT_DB_PATH) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_connection(db_path) as conn:
        conn.execute("UPDATE matches SET feedback = ?, updated_at = ? WHERE id = ?", (feedback, now, match_id))
        conn.commit()


def get_matches_for_dashboard(
    status: str | None = None,
    min_score: int | None = None,
    limit: int | None = 100,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    query = """
    SELECT
        m.id AS match_id,
        m.score,
        m.reason,
        m.sources AS match_sources,
        m.status,
        m.feedback,
        m.created_at AS matched_at,
        o.id AS opening_id,
        o.title,
        o.seniority,
        o.location AS opening_location,
        o.remote,
        o.apply_url,
        o.source,
        o.posted_at,
        o.status_head_ok,
        c.id AS company_id,
        c.name AS company_name,
        c.domain AS company_domain,
        c.stage,
        c.funding,
        c.location AS company_location,
        c.founders,
        c.summary AS company_summary,
        c.sources AS company_sources
    FROM matches m
    JOIN openings o ON m.opening_id = o.id
    JOIN companies c ON o.company_id = c.id
    WHERE 1=1
    """
    params: list[Any] = []
    if status and status != "all":
        query += " AND m.status = ?"
        params.append(status)
    if min_score is not None:
        query += " AND m.score >= ?"
        params.append(min_score)

    query += " ORDER BY m.score DESC, m.created_at DESC"
    if limit:
        query += " LIMIT ?"
        params.append(limit)

    with get_connection(db_path) as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            if d.get("match_sources"):
                try:
                    d["match_sources"] = json.loads(d["match_sources"])
                except Exception:
                    d["match_sources"] = []
            else:
                d["match_sources"] = []

            if d.get("company_sources"):
                try:
                    d["company_sources"] = json.loads(d["company_sources"])
                except Exception:
                    d["company_sources"] = []
            else:
                d["company_sources"] = []
            results.append(d)
        return results


def get_feedback_examples(limit: int = 10, db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    """Most recent good/bad feedback entries, for few-shot calibration in scoring prompts."""
    query = """
    SELECT o.title, c.name AS company_name, m.reason, m.feedback, m.score
    FROM matches m
    JOIN openings o ON m.opening_id = o.id
    JOIN companies c ON o.company_id = c.id
    WHERE m.feedback IS NOT NULL
    ORDER BY m.updated_at DESC
    LIMIT ?
    """
    with get_connection(db_path) as conn:
        rows = conn.execute(query, (limit,)).fetchall()
        return [dict(r) for r in rows]


def count_feedback_entries(db_path: Path = DEFAULT_DB_PATH) -> int:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) FROM matches WHERE feedback IS NOT NULL").fetchone()
        return int(row[0] or 0)


def record_discovered_ats_board(platform: str, slug: str, db_path: Path = DEFAULT_DB_PATH) -> None:
    """Records a Greenhouse/Lever/Ashby board slug found via Tavily search for the ATS
    sources to pick up starting next run (the auto-discovery growth loop). Stored as a
    single JSON-encoded list under the 'ats_discovered' cursor row."""
    discovered = get_discovered_ats_boards(db_path=db_path)
    if platform not in discovered:
        return
    if slug not in discovered[platform]:
        discovered[platform].append(slug)
        set_source_cursor("ats_discovered", json.dumps(discovered), db_path=db_path)


def get_discovered_ats_boards(db_path: Path = DEFAULT_DB_PATH) -> dict[str, list[str]]:
    """Returns {"greenhouse": [...], "lever": [...], "ashby": [...]} of slugs discovered
    by Tavily search in prior runs."""
    default: dict[str, list[str]] = {"greenhouse": [], "lever": [], "ashby": []}
    raw = get_source_cursor("ats_discovered", db_path=db_path)
    if not raw:
        return default
    try:
        data = json.loads(raw)
    except Exception:
        return default
    for platform in default:
        default[platform] = [s for s in data.get(platform, []) if isinstance(s, str)]
    return default


def record_run_start(sources: list[str], db_path: Path = DEFAULT_DB_PATH) -> int:
    now = datetime.now(timezone.utc).isoformat()
    sources_json = json.dumps(sources)
    with get_connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO runs (started_at, sources_checked, new_openings, errors) VALUES (?, ?, 0, '[]')",
            (now, sources_json),
        )
        conn.commit()
        return cur.lastrowid


def record_run_finish(run_id: int, new_openings: int, errors: list[str], db_path: Path = DEFAULT_DB_PATH) -> None:
    now = datetime.now(timezone.utc).isoformat()
    errors_json = json.dumps(errors)
    with get_connection(db_path) as conn:
        conn.execute(
            """UPDATE runs
               SET finished_at = ?, new_openings = ?, errors = ?
               WHERE id = ?""",
            (now, new_openings, errors_json, run_id),
        )
        conn.commit()


def get_source_cursor(source: str, db_path: Path = DEFAULT_DB_PATH) -> str | None:
    with get_connection(db_path) as conn:
        cur = conn.execute("SELECT cursor FROM source_cursors WHERE source = ?", (source,))
        row = cur.fetchone()
        return row["cursor"] if row else None


def set_source_cursor(source: str, cursor: str, db_path: Path = DEFAULT_DB_PATH) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_connection(db_path) as conn:
        conn.execute(
            """INSERT INTO source_cursors (source, last_checked_at, cursor)
               VALUES (?, ?, ?)
               ON CONFLICT(source) DO UPDATE SET
                   last_checked_at = excluded.last_checked_at,
                   cursor = excluded.cursor""",
            (source, now, cursor),
        )
        conn.commit()


def record_api_cost(provider: str, endpoint: str, cost_cents: float, db_path: Path = DEFAULT_DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO api_usage (provider, endpoint, cost_cents) VALUES (?, ?, ?)",
            (provider, endpoint, cost_cents),
        )
        conn.commit()


def get_monthly_api_cost_cents(db_path: Path = DEFAULT_DB_PATH) -> float:
    # First day of current month in UTC
    now = datetime.now(timezone.utc)
    first_of_month = datetime(now.year, now.month, 1, tzinfo=timezone.utc).isoformat()
    with get_connection(db_path) as conn:
        cur = conn.execute("SELECT SUM(cost_cents) as total FROM api_usage WHERE timestamp >= ?", (first_of_month,))
        row = cur.fetchone()
        return float(row["total"] or 0.0)


def get_pipeline_stats(db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    with get_connection(db_path) as conn:
        total_openings = conn.execute("SELECT COUNT(*) FROM openings").fetchone()[0]
        total_matches = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
        new_matches = conn.execute("SELECT COUNT(*) FROM matches WHERE status = 'new'").fetchone()[0]
        applied = conn.execute("SELECT COUNT(*) FROM matches WHERE status = 'applied'").fetchone()[0]
        interviewing = conn.execute("SELECT COUNT(*) FROM matches WHERE status = 'interviewing'").fetchone()[0]
        saved = conn.execute("SELECT COUNT(*) FROM matches WHERE status = 'saved'").fetchone()[0]

        last_run = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()

        return {
            "total_openings": total_openings,
            "total_matches": total_matches,
            "new_matches": new_matches,
            "applied": applied,
            "interviewing": interviewing,
            "saved": saved,
            "last_run": dict(last_run) if last_run else None,
            "monthly_cost_cents": get_monthly_api_cost_cents(db_path),
        }
