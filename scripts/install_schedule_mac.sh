#!/usr/bin/env bash
# Installs (or removes with --uninstall) a launchd agent that runs
# `python -m radar run` every `schedule_hours` hours (default 6),
# including catch-up runs after the laptop wakes from sleep.
set -euo pipefail

LABEL="com.jobradar.run"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${REPO_DIR}/.venv/bin/python"
INTERVAL_HOURS="${SCHEDULE_HOURS:-6}"
INTERVAL_SECONDS=$((INTERVAL_HOURS * 3600))

if [[ "${1:-}" == "--uninstall" ]]; then
    launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Uninstalled ${LABEL}."
    exit 0
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "No venv python found at $PYTHON_BIN. Create one first: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi

mkdir -p "${REPO_DIR}/logs"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON_BIN}</string>
        <string>-m</string>
        <string>radar</string>
        <string>run</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${REPO_DIR}</string>
    <key>StartInterval</key>
    <integer>${INTERVAL_SECONDS}</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${REPO_DIR}/logs/scheduler.log</string>
    <key>StandardErrorPath</key>
    <string>${REPO_DIR}/logs/scheduler.err.log</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo "Installed ${LABEL}, running every ${INTERVAL_HOURS}h (launchd catches up missed runs on wake)."
echo "Uninstall with: $0 --uninstall"
