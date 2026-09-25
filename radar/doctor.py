"""Environment/config health checks for Job Radar (`python -m radar doctor`)."""

import os
from collections.abc import Callable
from pathlib import Path

import httpx
import yaml

from radar.budget import get_configured_spend_cap
from radar.db import DEFAULT_DB_PATH, get_monthly_api_cost_cents, init_db

CheckResult = tuple[bool, str]


def _check_config(config_path: str) -> CheckResult:
    path = Path(config_path)
    if not path.exists():
        return False, f"{config_path} not found"
    try:
        with open(path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except Exception as e:
        return False, f"{config_path} failed to parse: {e}"
    if not config.get("targets"):
        return False, f"{config_path} is missing a 'targets' section"
    return True, f"{config_path} loaded ({len(config)} top-level keys)"


def _check_env_keys() -> CheckResult:
    missing = [k for k in ("ANTHROPIC_API_KEY", "TAVILY_API_KEY") if not os.getenv(k)]
    if missing:
        return True, f"optional keys not set: {', '.join(missing)} (heuristic fallback will be used)"
    return True, "ANTHROPIC_API_KEY and TAVILY_API_KEY are set"


def _check_ollama() -> CheckResult:
    host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    model = os.getenv("OLLAMA_MODEL", "llama3.2")
    try:
        with httpx.Client(timeout=5.0) as client:
            res = client.get(f"{host}/api/tags")
            if res.status_code != 200:
                return False, f"Ollama at {host} responded with {res.status_code}"
            names = [m.get("name", "") for m in res.json().get("models", [])]
            if names and not any(model in n for n in names):
                return (
                    False,
                    f"Ollama reachable but model '{model}' not pulled (found: {', '.join(names) or 'none'})",
                )
            return True, f"Ollama reachable at {host}"
    except Exception as e:
        return False, f"Ollama not reachable at {host}: {e} (Claude/heuristic fallback will be used)"


def _check_anthropic() -> CheckResult:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return True, "ANTHROPIC_API_KEY not set, skipping live check (heuristic fallback active)"
    model = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
    try:
        with httpx.Client(timeout=15.0) as client:
            res = client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={"model": model, "max_tokens": 4, "messages": [{"role": "user", "content": "ping"}]},
            )
            if res.status_code == 200:
                return True, "Anthropic API key works"
            return False, f"Anthropic API call failed: {res.status_code} {res.text[:200]}"
    except Exception as e:
        return False, f"Anthropic API call failed: {e}"


def _check_tavily() -> CheckResult:
    key = os.getenv("TAVILY_API_KEY")
    if not key:
        return True, "TAVILY_API_KEY not set, skipping live check (ATS/HN sources still work)"
    try:
        with httpx.Client(timeout=15.0) as client:
            res = client.post(
                "https://api.tavily.com/search",
                json={"api_key": key, "query": "ping", "max_results": 1},
            )
            if res.status_code == 200:
                return True, "Tavily API key works"
            return False, f"Tavily API call failed: {res.status_code} {res.text[:200]}"
    except Exception as e:
        return False, f"Tavily API call failed: {e}"


def _check_db(db_path: Path) -> CheckResult:
    try:
        init_db(db_path)
        return True, f"DB schema OK at {db_path}"
    except Exception as e:
        return False, f"DB init failed: {e}"


def _check_resume(config_path: str) -> CheckResult:
    default = Path("data/resume.pdf")
    if default.exists():
        return True, f"resume found at {default}"
    return True, f"no resume at {default} (optional; profile.json can be hand-edited instead)"


def _check_profile(profile_path: str) -> CheckResult:
    path = Path(profile_path)
    if not path.exists():
        return True, f"{profile_path} not found yet; run `python -m radar profile`"
    return True, f"{profile_path} exists"


def _check_scheduler() -> CheckResult:
    scripts = list(Path("scripts").glob("install_schedule_*"))
    if not scripts:
        return False, "no scheduler install scripts found in scripts/"
    return True, f"{len(scripts)} scheduler script(s) available in scripts/"


def _check_spend(db_path: Path) -> CheckResult:
    try:
        spent = get_monthly_api_cost_cents(db_path)
        cap = get_configured_spend_cap()
        return True, f"this month's spend: {spent:.0f}c / {cap:.0f}c cap"
    except Exception as e:
        return False, f"could not compute spend: {e}"


def _check_gitignore() -> CheckResult:
    path = Path(".gitignore")
    if not path.exists():
        return False, ".gitignore not found"
    text = path.read_text(encoding="utf-8")
    required = [".env", "*.sqlite", "profile.json", "logs/"]
    missing = [r for r in required if r not in text]
    if missing:
        return False, f".gitignore is missing: {', '.join(missing)}"
    return True, ".gitignore covers secrets and generated files"


def run_doctor(config_path: str = "config.yaml", profile_path: str = "profile.json", db_path: Path = DEFAULT_DB_PATH) -> bool:
    checks: list[tuple[str, Callable[[], CheckResult]]] = [
        ("config.yaml valid", lambda: _check_config(config_path)),
        ("env keys present", _check_env_keys),
        ("Ollama reachable", _check_ollama),
        ("Anthropic key works", _check_anthropic),
        ("Tavily key works", _check_tavily),
        ("DB schema", lambda: _check_db(db_path)),
        ("resume file", lambda: _check_resume(config_path)),
        ("profile.json exists", lambda: _check_profile(profile_path)),
        ("scheduler installed", _check_scheduler),
        ("monthly spend vs cap", lambda: _check_spend(db_path)),
        ("gitignore covers secrets", _check_gitignore),
    ]

    all_ok = True
    print("Job Radar doctor\n" + "-" * 40)
    for label, check in checks:
        try:
            ok, detail = check()
        except Exception as e:
            ok, detail = False, f"check crashed: {e}"
        all_ok = all_ok and ok
        icon = "✅" if ok else "❌"
        print(f"{icon} {label}: {detail}")

    print("-" * 40)
    print("All critical checks passed." if all_ok else "Some checks failed — see ❌ lines above.")
    return all_ok
