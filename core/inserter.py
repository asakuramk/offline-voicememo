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


class TextInserter:
    def insert(self, text: str):
        try:
            original = pyperclip.paste()
        except Exception:
            original = ""

        try:
            pyperclip.copy(text)
            time.sleep(0.15)  # Wait for clipboard to be ready
            pyautogui.hotkey("command", "v")
        finally:
            threading.Thread(target=self._restore, args=(original,), daemon=True).start()

    def _restore(self, original: str):
        time.sleep(0.8)  # Wait until paste is complete before restoring
        try:
            pyperclip.copy(original)
        except Exception:
            pass
