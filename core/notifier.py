"""
macOS notification + system-sound helpers via osascript / afplay.
Non-critical — failures are silently ignored.
"""
import subprocess


def notify(title: str, message: str):
    # Pass title/message as script arguments (argv) rather than interpolating
    # them into the AppleScript source, so no escaping/injection is possible.
    script = (
        "on run argv\n"
        'display notification (item 1 of argv) '
        'with title "VoiceMemo" subtitle (item 2 of argv)\n'
        "end run"
    )
    try:
        subprocess.run(
            ["osascript", "-e", script, str(message), str(title)],
            capture_output=True,
            timeout=3,
        )
    except Exception:
        pass


def play_sound(name: str):
    """Play a built-in macOS system sound by name (e.g. 'Tink', 'Pop')."""
    path = f"/System/Library/Sounds/{name}.aiff"
    try:
        subprocess.Popen(
            ["afplay", path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass
