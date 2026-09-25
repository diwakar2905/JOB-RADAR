"""Desktop notification system for top Job Radar matches."""

import os
import subprocess
import sys


def notify(title: str, message: str) -> None:
    """Send cross-platform non-blocking notification. Never blocks execution."""
    print(f"\n[JOB RADAR ALERT] {title}: {message}\n", flush=True)

    if sys.platform == "win32":
        try:
            # Completely non-blocking background toast via PowerShell
            # BurntToast or Windows.UI.Notifications
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
        except Exception:
            pass
