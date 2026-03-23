"""
Inserts text at the current cursor position by:
1. Saving the user's clipboard contents
2. Copying the result text to the clipboard
3. Simulating Cmd+V to paste into the active window
4. Restoring the original clipboard after a short delay
"""
import threading
import time
import pyperclip
import pyautogui

# Disable fail-safe (top-left corner abort) — not needed for keyboard-only use
pyautogui.FAILSAFE = False


class TextInserter:
    def insert(self, text: str):
        try:
            original = pyperclip.paste()
        except Exception:
            original = ""

        try:
            pyperclip.copy(text)
            time.sleep(0.2)   # Let clipboard settle
            pyautogui.hotkey("command", "v")
            time.sleep(0.1)   # Let paste complete
        except Exception:
            pass
        finally:
            threading.Thread(target=self._restore, args=(original,), daemon=True).start()

    def _restore(self, original: str):
        time.sleep(0.8)  # Wait until paste is complete before restoring
        try:
            pyperclip.copy(original)
        except Exception:
            pass
