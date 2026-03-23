"""
Offline Voice Memo Processor
Menu bar app for macOS.

Usage:
    python main.py

Hotkey (default: Option key):
    Press once  -> start recording
    Press again -> stop recording, transcribe, AI-edit, paste at cursor
"""
import json
import queue
import threading
from datetime import datetime
from pathlib import Path

import rumps

from config.config_manager import ConfigManager
from core.hotkey import HotkeyListener
from core.inserter import TextInserter
from core.llm_client import LLMClient
from core.notifier import notify
from core.recorder import Recorder
from core.transcriber import Transcriber

BASE_DIR = Path(__file__).parent

ICON_IDLE       = "mic"
ICON_RECORDING  = "[録音中]"
ICON_TRANSCRIBE = "[文字起こし中]"
ICON_AI         = "[AI解析中]"


class VoiceMemoApp(rumps.App):
    def __init__(self):
        super().__init__("mic", quit_button="終了")

        self.config   = ConfigManager(BASE_DIR / "config" / "settings.json")
        self.settings = self.config.load()

        self.recorder    = Recorder()
        self.transcriber = Transcriber(self.settings)
        self.llm         = LLMClient(self.settings)
        self.inserter    = TextInserter()

        self._state_lock    = threading.Lock()
        self._is_recording  = False
        self._is_processing = False
        self._last_result   = ""

        # UI update queue: background threads post lambdas here;
        # _drain_ui_queue() runs them safely on the main thread.
        self._ui_queue = queue.Queue()
        self._ui_timer = rumps.Timer(self._drain_ui_queue, 0.05)
        self._ui_timer.start()

        # --- Menu ---
        self._toggle_item = rumps.MenuItem(
            "録音開始  [Option]", callback=self.toggle_recording
        )
        self._copy_item = rumps.MenuItem(
            "最後の結果をコピー", callback=self.copy_last_result
        )
        self._template_menu = rumps.MenuItem("テンプレート")
        self._reload_item = rumps.MenuItem(
            "設定を再読み込み", callback=self.reload_settings
        )

        self.menu = [
            self._toggle_item,
            None,
            self._copy_item,
            None,
            self._template_menu,
            None,
            self._reload_item,
        ]

        self._build_template_menu()

        # --- Global hotkey listener (runs in background thread) ---
        self._hotkey_listener = HotkeyListener(
            hotkey=self.settings.get("hotkey", "alt"),
            callback=self._on_hotkey,
        )
        self._hotkey_listener.start()

    # ------------------------------------------------------------------
    # Main-thread dispatcher
    # ------------------------------------------------------------------

    def _ui(self, func):
        """Schedule func() to run on the main thread via the timer loop."""
        self._ui_queue.put(func)

    def _drain_ui_queue(self, _):
        while True:
            try:
                self._ui_queue.get_nowait()()
            except queue.Empty:
                break

    # ------------------------------------------------------------------
    # Hotkey callback (called from pynput background thread)
    # ------------------------------------------------------------------

    def _on_hotkey(self):
        """Dispatch toggle to the main thread so UI updates are safe."""
        self._ui(self._toggle_recording_main)

    # ------------------------------------------------------------------
    # Recording toggle (must run on main thread)
    # ------------------------------------------------------------------

    def toggle_recording(self, sender=None):
        """Called when the menu item is clicked (already on main thread)."""
        self._toggle_recording_main()

    def _toggle_recording_main(self):
        with self._state_lock:
            if self._is_processing:
                notify("処理中", "前の録音を処理中です")
                return
            if not self._is_recording:
                self._start_recording()
            else:
                self._stop_and_process()

    def _start_recording(self):
        self._is_recording = True
        self.title = ICON_RECORDING
        self._toggle_item.title = "録音停止  [Option]"
        self.recorder.start()
        notify("録音開始", "Optionキーを再度押すと停止します")

    def _stop_and_process(self):
        self._is_recording  = False
        self._is_processing = True
        self._toggle_item.title = "録音開始  [Option]"
        audio_path = self.recorder.stop()
        threading.Thread(
            target=self._process_audio, args=(audio_path,), daemon=True
        ).start()

    # ------------------------------------------------------------------
    # Processing pipeline (background thread)
    # ------------------------------------------------------------------

    def _process_audio(self, audio_path: Path):
        try:
            self._ui(lambda: setattr(self, "title", ICON_TRANSCRIBE))
            notify("文字起こし中...", "")

            raw_text = self.transcriber.transcribe(audio_path)
            if not raw_text.strip():
                notify("認識失敗", "音声が認識できませんでした")
                return

            self._ui(lambda: setattr(self, "title", ICON_AI))
            notify("AI解析中...", raw_text[:80])

            try:
                processed = self.llm.process(raw_text)
            except Exception as llm_err:
                # LLM failed — fall back to raw transcription
                notify("LLMエラー (生テキストを使用)", str(llm_err)[:80])
                processed = raw_text

            self._last_result = processed
            self.inserter.insert(processed)
            notify("完了", processed[:100])
            self._save_session(audio_path, raw_text, processed)

        except Exception as e:
            notify("エラー", str(e)[:120])
        finally:
            with self._state_lock:
                self._is_processing = False
            self._ui(lambda: setattr(self, "title", ICON_IDLE))

    # ------------------------------------------------------------------
    # Menu callbacks
    # ------------------------------------------------------------------

    def copy_last_result(self, sender):
        if self._last_result:
            import pyperclip
            pyperclip.copy(self._last_result)
            notify("コピーしました", self._last_result[:80])
        else:
            notify("結果なし", "まだ録音・処理していません")

    def reload_settings(self, sender):
        self.settings = self.config.load()
        self.transcriber.update_settings(self.settings)
        self.llm.update_settings(self.settings)
        self._build_template_menu()
        notify("設定再読み込み完了", "")

    def _build_template_menu(self):
        # _menu is None until at least one item is added;
        # guard against crash on first call.
        if self._template_menu._menu is not None:
            self._template_menu.clear()

        active = self.settings.get("active_template", "summary")

        # Fixed templates (key, display label); None = separator
        FIXED = [
            ("minutes",         "議事録"),
            ("summary",         "要約"),
            ("raw",             "そのまま出力 (LLMなし)"),
            None,
            ("shosin",          "[医療] 問診"),
            ("medical_summary", "[医療] 医療サマリー"),
            ("soap",            "[医療] SOAP"),
        ]
        fixed_keys = {row[0] for row in FIXED if row is not None}

        # User-created .txt files not in the fixed list
        custom_entries = [
            (f.stem, f"カスタム: {f.stem}")
            for f in sorted((BASE_DIR / "templates").glob("*.txt"))
            if f.stem not in fixed_keys
        ]

        entries = list(FIXED)
        if custom_entries:
            entries.append(None)
            entries.extend(custom_entries)

        for row in entries:
            if row is None:
                self._template_menu.add(rumps.separator)
                continue
            key, label = row
            prefix = "* " if key == active else "  "
            self._template_menu.add(rumps.MenuItem(
                f"{prefix}{label}",
                callback=self._make_template_callback(key),
            ))

        # --- Custom prompt editor ---
        self._template_menu.add(rumps.separator)
        self._template_menu.add(
            rumps.MenuItem("プロンプトを編集...", callback=self.edit_custom_prompt)
        )

    def _make_template_callback(self, template_key: str):
        def callback(sender):
            self.settings["active_template"] = template_key
            self.config.save(self.settings)
            self.llm.update_settings(self.settings)
            self._build_template_menu()
            notify("テンプレート変更", template_key)
        return callback

    def edit_custom_prompt(self, sender):
        """Open a text-input window to create/edit a custom prompt template."""
        custom_path = BASE_DIR / "templates" / "custom.txt"
        current = (
            custom_path.read_text(encoding="utf-8")
            if custom_path.exists()
            else "以下の音声文字起こしを整形してください。\n\n# 文字起こし\n{text}"
        )
        win = rumps.Window(
            message=(
                "カスタムプロンプトを入力してください。\n"
                "{text} の部分に文字起こし結果が挿入されます。"
            ),
            title="カスタムプロンプトの編集",
            default_text=current,
            ok="保存して選択",
            cancel="キャンセル",
            dimensions=(520, 260),
        )
        response = win.run()
        if response.clicked and response.text.strip():
            custom_path.write_text(response.text.strip(), encoding="utf-8")
            self.settings["active_template"] = "custom"
            self.config.save(self.settings)
            self.llm.update_settings(self.settings)
            self._build_template_menu()
            notify("カスタムプロンプトを保存しました", "テンプレート: custom")

    # ------------------------------------------------------------------
    # Session storage
    # ------------------------------------------------------------------

    def _save_session(self, audio_path: Path, raw_text: str, processed: str):
        sessions_dir = BASE_DIR / "data" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now()
        record = {
            "timestamp":      ts.isoformat(),
            "audio_path":     str(audio_path),
            "raw_text":       raw_text,
            "processed_text": processed,
            "template":       self.settings.get("active_template", "memo"),
        }
        out = sessions_dir / f"{ts.strftime('%Y%m%d_%H%M%S')}.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

if __name__ == "__main__":
    for d in ["data/audio", "data/sessions", "models"]:
        (BASE_DIR / d).mkdir(parents=True, exist_ok=True)

    VoiceMemoApp().run()
