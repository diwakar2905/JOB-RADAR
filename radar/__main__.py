"""CLI entry point: `python -m radar <doctor|profile|run>`."""

import argparse
import sys

from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m radar", description="Job Radar CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="Check config, keys, Ollama, and DB health")

    profile_p = sub.add_parser("profile", help="Build/refresh profile.json")
    profile_p.add_argument("--resume", help="Path to resume PDF or TXT file")
    profile_p.add_argument("--github", help="GitHub username or profile URL")
    profile_p.add_argument("--site", help="Personal website or portfolio URL")
    profile_p.add_argument("--output", default="profile.json", help="Path to output profile.json")

    run_p = sub.add_parser("run", help="Run one discovery pipeline pass")
    run_p.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    run_p.add_argument("--profile", default="profile.json", help="Path to profile.json")
    run_p.add_argument("--dry-run", action="store_true", help="Run without persisting to SQLite")
    run_p.add_argument("--source", help="Run only a specific source (ats, hn, tavily, yc)")
    run_p.add_argument("--limit", type=int, help="Max new matches to discover")

    args = parser.parse_args()

    if args.command == "doctor":
        from radar.doctor import run_doctor

        ok = run_doctor()
        sys.exit(0 if ok else 1)

    elif args.command == "profile":
        from radar.profile import build_profile

        if args.resume:
            print(f"Reading resume from {args.resume}...")
        if args.github:
            print(f"Fetching GitHub data for {args.github}...")
        if args.site:
            print(f"Fetching portfolio text from {args.site}...")

        build_profile(resume_path=args.resume, github=args.github, site=args.site, output=args.output)
        print(f"Updated profile saved to {args.output}")
        print("Review it by hand before your next run.")

    elif args.command == "run":
        from run import acquire_lock, execute_pipeline, release_lock

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
