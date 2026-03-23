"""
macOS notification helper via osascript.
Non-critical — failures are silently ignored.
"""
import subprocess


def notify(title: str, message: str):
    safe_title = title.replace('"', "'")
    safe_msg = message.replace('"', "'")
    script = (
        f'display notification "{safe_msg}" '
        f'with title "VoiceMemo" subtitle "{safe_title}"'
    )
    try:
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            timeout=3,
        )
    except Exception:
        pass
