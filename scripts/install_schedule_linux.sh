#!/usr/bin/env bash
# Installs (or removes with --uninstall) a crontab line that runs
# `python -m radar run` every `schedule_hours` hours (default 6).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${REPO_DIR}/.venv/bin/python"
INTERVAL_HOURS="${SCHEDULE_HOURS:-6}"
MARKER="# job-radar-scheduled-run"
CRON_LINE="0 */${INTERVAL_HOURS} * * * cd ${REPO_DIR} && ${PYTHON_BIN} -m radar run >> ${REPO_DIR}/logs/cron.log 2>&1 ${MARKER}"

if [[ "${1:-}" == "--uninstall" ]]; then
    (crontab -l 2>/dev/null | grep -vF "$MARKER") | crontab -
    echo "Uninstalled Job Radar crontab entry."
    exit 0
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "No venv python found at $PYTHON_BIN. Create one first: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi

mkdir -p "${REPO_DIR}/logs"

( crontab -l 2>/dev/null | grep -vF "$MARKER"; echo "$CRON_LINE" ) | crontab -

echo "Installed crontab entry, running every ${INTERVAL_HOURS}h:"
echo "  $CRON_LINE"
echo "Uninstall with: $0 --uninstall"
