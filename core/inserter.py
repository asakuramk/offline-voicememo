"""
Inserts text at the current cursor position by:
1. Saving the user's clipboard contents
2. Writing the result to the clipboard marked as *concealed* so that
   clipboard-history apps ignore it and iCloud Universal Clipboard does not
   sync it to other devices (protects patient information from leaving the Mac)
3. Simulating Cmd+V to paste into the active window
4. Restoring the original clipboard after a short delay; if restore fails the
   clipboard is cleared so sensitive text is never left behind
"""
import threading
import time
import pyautogui
from AppKit import NSPasteboard

# Keep pyautogui's fail-safe enabled (default). We only send Cmd+V (no mouse
# movement), so it never triggers in normal use but stays available as a guard.
pyautogui.FAILSAFE = True

# UTI for plain text on the general pasteboard.
TEXT_TYPE = "public.utf8-plain-text"
# De-facto standard marker telling clipboard managers not to record this entry.
# https://nspasteboard.org/  — also keeps concealed data out of Universal Clipboard.
CONCEALED_TYPE = "org.nspasteboard.ConcealedType"


class TextInserter:
    def __init__(self):
        self._pb = NSPasteboard.generalPasteboard()

    # ------------------------------------------------------------------
    # Pasteboard helpers
    # ------------------------------------------------------------------

    def _read(self) -> str:
        try:
            return self._pb.stringForType_(TEXT_TYPE) or ""
        except Exception:
            return ""

    def _write_concealed(self, text: str):
        """Write text marked concealed (excluded from history / Universal Clipboard)."""
        self._pb.clearContents()
        self._pb.declareTypes_owner_([TEXT_TYPE, CONCEALED_TYPE], None)
        self._pb.setString_forType_(text, TEXT_TYPE)
        self._pb.setString_forType_(text, CONCEALED_TYPE)

    def _write_plain(self, text: str):
        self._pb.clearContents()
        self._pb.declareTypes_owner_([TEXT_TYPE], None)
        self._pb.setString_forType_(text, TEXT_TYPE)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def insert(self, text: str):
        original = self._read()

        try:
            self._write_concealed(text)
            time.sleep(0.2)   # Let clipboard settle
            pyautogui.hotkey("command", "v")
            time.sleep(0.1)   # Let paste complete
        except Exception:
            pass
        finally:
            threading.Thread(target=self._restore, args=(original,), daemon=True).start()

    def copy(self, text: str):
        """Copy text to the clipboard for the 'copy last result' menu (concealed)."""
        try:
            self._write_concealed(text)
        except Exception:
            pass

    def _restore(self, original: str):
        time.sleep(0.8)  # Wait until paste is complete before restoring
        try:
            if original:
                self._write_plain(original)
            else:
                # Nothing to restore — clear so the concealed result is not left behind.
                self._pb.clearContents()
        except Exception:
            try:
                self._pb.clearContents()
            except Exception:
                pass
