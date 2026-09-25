"""Monthly API spend cap tracking and guardrails."""

import os
from pathlib import Path
from typing import Dict, Any, Optional
from radar.db import record_api_cost, get_monthly_api_cost_cents, DEFAULT_DB_PATH


def get_configured_spend_cap(config: Optional[Dict[str, Any]] = None) -> float:
    """Return configured monthly cap in cents (defaults to 500 cents = $5.00)."""
    env_cap = os.getenv("MONTHLY_SPEND_CAP_CENTS")
    if env_cap:
        try:
            return float(env_cap)
        except ValueError:
            pass

    if config and "monthly_spend_cap_cents" in config:
        return float(config["monthly_spend_cap_cents"])

    return 500.0  # $5.00 limit


def is_budget_exceeded(config: Optional[Dict[str, Any]] = None, db_path: Path = DEFAULT_DB_PATH) -> bool:
    """Returns True if current month's estimated API spend exceeds the cap."""
    cap = get_configured_spend_cap(config)
    spent = get_monthly_api_cost_cents(db_path)
    return spent >= cap


def record_cost(provider: str, endpoint: str, cost_cents: float, db_path: Path = DEFAULT_DB_PATH) -> None:
    """Records an API cost line item in SQLite."""
    record_api_cost(provider, endpoint, cost_cents, db_path)
