"""Desktop notification system for top Job Radar matches."""

import os
import subprocess
import sys


def _sanitize(text: str, max_len: int = 200) -> str:
    """Strips characters that could break out of a shell/PowerShell string
    literal. Notification text is built from job titles and company names
    pulled from external sources (job boards, HN comments), so it must never
    be interpolated into a command string unescaped."""
    cleaned = "".join(c for c in text if c not in "\"'`$\\\n\r")
    return cleaned[:max_len]


def notify(title: str, message: str) -> None:
    """Send cross-platform non-blocking notification. Never blocks execution."""
    print(f"\n[JOB RADAR ALERT] {title}: {message}\n", flush=True)

    safe_title = _sanitize(title)
    safe_message = _sanitize(message)

    try:
        if sys.platform == "win32":
            _notify_windows(safe_title, safe_message)
        elif sys.platform == "darwin":
            _notify_macos(safe_title, safe_message)
        else:
            _notify_linux(safe_title, safe_message)
    except Exception:
        pass


def _notify_windows(title: str, message: str) -> None:
    ps_script = f"""
    try {{
        [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
        $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
        $textNodes = $template.GetElementsByTagName("text")
        $textNodes.Item(0).AppendChild($template.CreateTextNode("{title}")) | Out-Null
        $textNodes.Item(1).AppendChild($template.CreateTextNode("{message}")) | Out-Null
        $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("JobRadar")
        $notification = [Windows.UI.Notifications.ToastNotification]::new($template)
        $notifier.Show($notification)
    }} catch {{}}
    """
    subprocess.Popen(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _notify_macos(title: str, message: str) -> None:
    script = f'display notification "{message}" with title "{title}"'
    subprocess.Popen(["osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _notify_linux(title: str, message: str) -> None:
    subprocess.Popen(["notify-send", title, message], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
