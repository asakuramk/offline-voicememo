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
from core.dictionary import Dictionary
from core.hotkey import HotkeyListener
from core.inserter import TextInserter
from core.llm_client import LLMClient
from core.notifier import notify
from core.recorder import Recorder
from core.transcriber import Transcriber

BASE_DIR = Path(__file__).parent

ICON_RECORDING  = "mic  [録音中]"
ICON_TRANSCRIBE = "mic  [文字起こし中]"
ICON_AI         = "mic  [AI解析中]"


class VoiceMemoApp(rumps.App):
    def __init__(self):
        super().__init__("mic", quit_button="終了")

        self.config   = ConfigManager(BASE_DIR / "config" / "settings.json")
        self.settings = self.config.load()

        self.recorder    = Recorder()
        self.transcriber = Transcriber(self.settings)
        self.llm         = LLMClient(self.settings)
        self.inserter    = TextInserter()
        self.dictionary  = Dictionary()

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
        self._template_menu      = rumps.MenuItem("テンプレートを選択")
        self._edit_template_menu = rumps.MenuItem("テンプレートを編集")
        self._llm_mode_item = rumps.MenuItem(
            self._llm_mode_label(), callback=self.toggle_llm_mode
        )
        self._online_config_item = rumps.MenuItem(
            "オンライン設定...", callback=self.configure_online
        )
        self._llm_test_item = rumps.MenuItem(
            "LLM接続テスト...", callback=self.test_llm_connection
        )
        self._dict_item = rumps.MenuItem(
            "変換辞書を編集...", callback=self.edit_dictionary
        )
        self._show_raw_item = rumps.MenuItem(
            self._show_raw_label(), callback=self.toggle_show_raw
        )
        self._reload_item = rumps.MenuItem(
            "設定を再読み込み", callback=self.reload_settings
        )

        self.menu = [
            self._toggle_item,
            None,
            self._copy_item,
            None,
            self._template_menu,
            self._edit_template_menu,
            None,
            self._llm_mode_item,
            self._online_config_item,
            self._llm_test_item,
            None,
            self._dict_item,
            self._show_raw_item,
            None,
            self._reload_item,
        ]

        self._build_template_menu()
        self._build_edit_template_menu()
        self.title = self._idle_title()

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

            # Apply user dictionary before sending to LLM
            raw_text = self.dictionary.apply(raw_text)

            self._ui(lambda: setattr(self, "title", ICON_AI))
            notify("AI解析中...", raw_text[:80])

            llm_error_msg = None
            try:
                processed = self.llm.process(raw_text)
            except Exception as llm_err:
                llm_error_msg = str(llm_err)
                processed = raw_text

            if llm_error_msg:
                err = llm_error_msg
                self._ui(lambda: rumps.alert(
                    title="LLM接続エラー",
                    message=f"{err}\n\n文字起こし原文をそのまま出力します。",
                ))

            # Build final output (with or without raw transcription)
            if self.settings.get("show_raw_text", False):
                ai_block = f"【⚠️ LLM未接続 - 生テキスト】\n{processed}" if llm_error_msg else f"【AI解析結果】\n{processed}"
                output = f"【文字起こし原文】\n{raw_text}\n\n{ai_block}"
            else:
                output = f"⚠️ LLM未接続\n{processed}" if llm_error_msg else processed

            self._last_result = output
            self.inserter.insert(output)
            notify("完了", processed[:100])
            self._save_session(audio_path, raw_text, processed)

        except Exception as e:
            notify("エラー", str(e)[:120])
        finally:
            with self._state_lock:
                self._is_processing = False
            self._ui(lambda: setattr(self, "title", self._idle_title()))

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

    # ------------------------------------------------------------------
    # LLM mode toggle
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # LLM connection test
    # ------------------------------------------------------------------

    def test_llm_connection(self, sender):
        notify("LLMテスト中...", "")
        threading.Thread(target=self._run_llm_test, daemon=True).start()

    def _run_llm_test(self):
        import socket
        url  = self.settings.get("lmstudio_url", "http://localhost:1234/v1")
        mode = self.settings.get("llm_mode", "offline")
        if mode == "online":
            url = self.settings.get("online_api_url", "https://api.openai.com/v1")

        # Step 1: TCP reachability check
        try:
            host = url.split("://")[-1].split("/")[0]
            host, port = (host.rsplit(":", 1) if ":" in host else (host, "1234"))
            sock = socket.create_connection((host, int(port)), timeout=2)
            sock.close()
        except Exception:
            step1_fail = True
        else:
            step1_fail = False

        if step1_fail:
            if mode == "offline":
                msg = (
                    f"ポート {port} に接続できません。\n\n"
                    "【手順】LM Studio を開く\n"
                    "  1. 左サイドバーの「←→」アイコンをクリック\n"
                    "  2. 上部でモデルを選択\n"
                    "  3. 「Start Server」ボタンを押す\n"
                    "  4. 「Server running on port 1234」と表示されたら再テスト"
                )
            else:
                msg = f"オンラインAPI ({url}) に到達できません。\nネットワーク接続またはURLを確認してください。"
            self._ui(lambda: rumps.alert(title="LLM接続テスト — 失敗 (到達不可)", message=msg))
            return

        # Step 2: API call test
        test_prompt = "「テスト成功」とだけ返してください。"
        try:
            client = self.llm._get_client()
            model  = self.llm._resolve_model(client)
            resp   = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": test_prompt}],
                temperature=0,
                max_tokens=50,
            )
            result = resp.choices[0].message.content.strip()
            self._ui(lambda: rumps.alert(
                title="LLM接続テスト — 成功",
                message=f"モード : {mode}\nモデル : {model}\n応答  : {result}",
            ))
        except Exception as e:
            err = str(e)
            self._ui(lambda: rumps.alert(
                title="LLM接続テスト — APIエラー",
                message=f"サーバーには接続できましたがAPIが応答しません。\n\n{err}\n\n"
                        "モデルがロード済みか確認してください。",
            ))

    def _idle_title(self) -> str:
        if self.settings.get("llm_mode", "offline") == "online":
            model = self.settings.get("online_model", "API")
            return f"mic  [ONLINE: {model}]"
        return "mic  [LOCAL]"

    def _llm_mode_label(self) -> str:
        mode = self.settings.get("llm_mode", "offline")
        if mode == "online":
            model = self.settings.get("online_model", "gpt-4o-mini")
            return f"LLM: [オンライン]  {model}"
        else:
            return "LLM: [オフライン]  LM Studio"

    def toggle_llm_mode(self, sender):
        current = self.settings.get("llm_mode", "offline")
        new_mode = "online" if current == "offline" else "offline"

        if new_mode == "online" and not self.settings.get("online_api_key", "").strip():
            # API key not set yet — open settings dialog first
            if not self._run_online_config_dialog():
                return  # user cancelled

        self.settings["llm_mode"] = new_mode
        self.config.save(self.settings)
        self.llm.update_settings(self.settings)
        self._llm_mode_item.title = self._llm_mode_label()
        self.title = self._idle_title()
        notify(
            "LLMモード切替",
            "オンライン (API)" if new_mode == "online" else "オフライン (LM Studio)",
        )

    def configure_online(self, sender):
        self._run_online_config_dialog()

    def _run_online_config_dialog(self) -> bool:
        """Show 3 dialogs to set online API URL / key / model. Returns True if saved."""
        # 1. API URL
        win = rumps.Window(
            message="オンラインAPIのエンドポイントURLを入力してください。\n(OpenAI互換であれば変更可)",
            title="オンライン設定 (1/3) — API URL",
            default_text=self.settings.get("online_api_url", "https://api.openai.com/v1"),
            ok="次へ",
            cancel="キャンセル",
            dimensions=(420, 30),
        )
        r = win.run()
        if not r.clicked:
            return False
        api_url = r.text.strip() or "https://api.openai.com/v1"

        # 2. API Key
        win = rumps.Window(
            message="APIキーを入力してください。\n(OpenAI: sk-...  /  Anthropic: sk-ant-...  など)",
            title="オンライン設定 (2/3) — APIキー",
            default_text=self.settings.get("online_api_key", ""),
            ok="次へ",
            cancel="キャンセル",
            dimensions=(420, 30),
        )
        r = win.run()
        if not r.clicked:
            return False
        api_key = r.text.strip()

        # 3. Model name
        win = rumps.Window(
            message="使用するモデル名を入力してください。\n例: gpt-4o-mini / gpt-4o / claude-opus-4-5",
            title="オンライン設定 (3/3) — モデル名",
            default_text=self.settings.get("online_model", "gpt-4o-mini"),
            ok="保存",
            cancel="キャンセル",
            dimensions=(420, 30),
        )
        r = win.run()
        if not r.clicked:
            return False
        model = r.text.strip() or "gpt-4o-mini"

        self.settings["online_api_url"] = api_url
        self.settings["online_api_key"] = api_key
        self.settings["online_model"]   = model
        self.config.save(self.settings)
        self.llm.update_settings(self.settings)
        self._llm_mode_item.title = self._llm_mode_label()
        self.title = self._idle_title()
        notify("オンライン設定を保存しました", f"モデル: {model}")
        return True

    # ------------------------------------------------------------------
    # Dictionary editor
    # ------------------------------------------------------------------

    def edit_dictionary(self, sender):
        win = rumps.Window(
            message=(
                "変換辞書を編集してください。\n"
                "書式:「変換前 = 変換後」を1行ずつ。# はコメント行。"
            ),
            title="変換辞書の編集",
            default_text=self.dictionary.to_editor_text(),
            ok="保存",
            cancel="キャンセル",
            dimensions=(520, 300),
        )
        response = win.run()
        if response.clicked:
            self.dictionary.from_editor_text(response.text)
            count = len(self.dictionary._entries)
            notify("変換辞書を保存しました", f"{count} 件のエントリ")

    # ------------------------------------------------------------------
    # Template editor submenu
    # ------------------------------------------------------------------

    # Keys that can be edited (builtin + any .txt file)
    EDITABLE_TEMPLATES = [
        ("minutes",         "議事録"),
        ("summary",         "要約"),
        (None, None),
        ("shosin",          "[医療] 問診"),
        ("medical_summary", "[医療] 医療サマリー"),
        ("soap",            "[医療] SOAP"),
    ]

    def _build_edit_template_menu(self):
        if self._edit_template_menu._menu is not None:
            self._edit_template_menu.clear()

        fixed_keys = {k for k, _ in self.EDITABLE_TEMPLATES if k}

        rows = list(self.EDITABLE_TEMPLATES)
        custom = [
            (f.stem, f"カスタム: {f.stem}")
            for f in sorted((BASE_DIR / "templates").glob("*.txt"))
            if f.stem not in fixed_keys
        ]
        if custom:
            rows.append((None, None))
            rows.extend(custom)

        for row in rows:
            if row is None or row[0] is None:
                self._edit_template_menu.add(rumps.separator)
                continue
            key, label = row
            self._edit_template_menu.add(rumps.MenuItem(
                label, callback=self._make_edit_template_callback(key)
            ))

    def _make_edit_template_callback(self, key: str):
        def callback(sender):
            self._open_template_editor(key)
        return callback

    def _open_template_editor(self, key: str):
        label_map = {k: l for k, l in self.EDITABLE_TEMPLATES if k}
        label = label_map.get(key, key)
        current = self.llm.get_template_content(key)
        win = rumps.Window(
            message=(
                f"テンプレート「{label}」を編集してください。\n"
                "{text} の部分に文字起こし結果が挿入されます。"
            ),
            title=f"テンプレートを編集: {label}",
            default_text=current,
            ok="保存",
            cancel="キャンセル",
            dimensions=(520, 320),
        )
        r = win.run()
        if r.clicked and r.text.strip():
            self.llm.save_template_content(key, r.text.strip())
            notify(f"テンプレートを保存しました", label)

    # ------------------------------------------------------------------
    # Show raw text toggle
    # ------------------------------------------------------------------

    def _show_raw_label(self) -> str:
        on = self.settings.get("show_raw_text", False)
        return "原文を表示: ON" if on else "原文を表示: OFF"

    def toggle_show_raw(self, sender):
        current = self.settings.get("show_raw_text", False)
        self.settings["show_raw_text"] = not current
        self.config.save(self.settings)
        self._show_raw_item.title = self._show_raw_label()
        state = "ON" if self.settings["show_raw_text"] else "OFF"
        notify("原文表示を変更しました", state)

    # ------------------------------------------------------------------

    def reload_settings(self, sender):
        self.settings = self.config.load()
        self.transcriber.update_settings(self.settings)
        self.llm.update_settings(self.settings)
        self.dictionary.load()
        self._build_template_menu()
        self._build_edit_template_menu()
        self._llm_mode_item.title = self._llm_mode_label()
        self._show_raw_item.title = self._show_raw_label()
        self.title = self._idle_title()
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
