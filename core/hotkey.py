"""
Global hotkey listener using pynput.
Detects Option (alt) key press without other modifier keys held.
Requires Accessibility permission for the terminal app running this script.
"""
import threading
from pynput import keyboard


class HotkeyListener:
    def __init__(self, hotkey: str, callback):
        """
        hotkey: "alt" | "alt_l" | "alt_r" | "ctrl" | "cmd"
        callback: called (in a daemon thread) each time the hotkey fires
        """
        self.hotkey = hotkey
        self.callback = callback
        self._pressed_keys: set = set()
        self._triggered = False
        self._lock = threading.Lock()
        self._listener = None

    def start(self):
        t = threading.Thread(target=self._listen, daemon=True)
        t.start()

    def stop(self):
        if self._listener:
            self._listener.stop()

    def _get_target_keys(self) -> set:
        mapping = {
            "alt":   {keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r},
            "alt_l": {keyboard.Key.alt_l},
            "alt_r": {keyboard.Key.alt_r},
            "ctrl":  {keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r},
            "cmd":   {keyboard.Key.cmd, keyboard.Key.cmd_l, keyboard.Key.cmd_r},
        }
        return mapping.get(self.hotkey, {keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r})

    def _listen(self):
        target_keys = self._get_target_keys()

        def on_press(key):
            with self._lock:
                self._pressed_keys.add(key)
                is_target = key in target_keys
                # Trigger only when Option is pressed and nothing else is held
                other_keys = self._pressed_keys - target_keys
                if is_target and not other_keys and not self._triggered:
                    self._triggered = True
                    threading.Thread(target=self.callback, daemon=True).start()

        def on_release(key):
            with self._lock:
                self._pressed_keys.discard(key)
                if key in target_keys:
                    self._triggered = False

        self._listener = keyboard.Listener(
            on_press=on_press,
            on_release=on_release,
        )
        self._listener.start()
        self._listener.join()
