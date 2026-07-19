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
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import rumps

from config.config_manager import ConfigManager
from core.dictionary import Dictionary
from core.hotkey import HotkeyListener
from core.inserter import TextInserter
from core.llm_client import LLMClient
from core.notifier import notify, play_sound
from core.recorder import Recorder
from core.secure_fs import harden, secure_dir
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
        self._purge_item = rumps.MenuItem(
            "保存データを全削除...", callback=self.purge_all_data
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
            self._purge_item,
            None,
            self._reload_item,
        ]

        self._build_template_menu()
        self._build_edit_template_menu()
        self.title = self._idle_title()

        # Enforce the retention window at startup.
        self._purge_old_data()

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
    # Notification content guard
    # ------------------------------------------------------------------

    def _preview(self, text: str, n: int = 80) -> str:
        """Return a text preview for notifications only if the user opted in.

        macOS notifications persist in Notification Center and appear on the
        lock screen, so transcription/AI content (which may contain patient
        information) is withheld unless notify_content_preview is enabled.
        """
        if self.settings.get("notify_content_preview", False):
            return text[:n]
        return ""

    def _ask_on_main(self, func):
        """Run func() on the main thread and block the caller until it returns.

        Lets a background pipeline thread show a modal dialog (which must run on
        the main thread) and wait for the user's decision.
        """
        result = {}
        done = threading.Event()

        def wrapper():
            try:
                result["value"] = func()
            finally:
                done.set()

        self._ui(wrapper)
        done.wait()
        return result.get("value")

    @staticmethod
    def _frontmost_app_name() -> str:
        """Name of the app that will receive the paste (captured before our dialog)."""
        try:
            from AppKit import NSWorkspace
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            return app.localizedName() if app else "不明"
        except Exception:
            return "不明"

    def _confirm_medical_insert(self, output: str, raw_text: str, app_name: str):
        """Modal confirmation for medical output. Returns (decision, edited_text).

        decision is one of "insert" | "copy" | "discard". edited_text carries any
        manual correction the user made (or None when discarded).

        Buttons are ok + add_button only (no cancel): rumps' Response.clicked
        collides when a cancel button and add_button are combined, so the default
        (safe) action is "コピーのみ" — it never auto-pastes into the active app.
        """
        message = (
            "⚠️ 医療テンプレートの結果です。挿入前に必ず内容を確認してください。\n"
            "・数値 / 薬剤名 / 用量 / 単位 は原文と必ず照合してください。\n"
            "・原文にない情報がAIによって追加されていないか確認してください。\n"
            f"・挿入先アプリ: {app_name}\n"
            "下のテキストはこの場で修正できます。\n\n"
            "【文字起こし原文（照合用）】\n"
            f"{raw_text}"
        )
        win = rumps.Window(
            message=message,
            title="医療テンプレート — 挿入前の確認",
            default_text=output,
            ok="コピーのみ",   # default / Enter — safe, never auto-pastes
            dimensions=(560, 320),
        )
        win.add_button("そのまま挿入")
        win.add_button("破棄")
        r = win.run()
        if r.clicked == 2:       # そのまま挿入
            return ("insert", r.text)
        if r.clicked == 3:       # 破棄
            return ("discard", None)
        return ("copy", r.text)  # コピーのみ (ok / default)

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
        if self.settings.get("record_sounds", True):
            play_sound("Tink")
        self.recorder.start()
        notify("録音開始", "Optionキーを再度押すと停止します")

    def _stop_and_process(self):
        self._is_recording  = False
        self._is_processing = True
        self._toggle_item.title = "録音開始  [Option]"
        if self.settings.get("record_sounds", True):
            play_sound("Pop")
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
            notify("AI解析中...", self._preview(raw_text))

            # 医療テンプレート使用中に online モードだと患者情報が外部APIへ
            # 送信されてしまう。medical_templates_offline_only が有効なら
            # 強制的にローカル処理へフォールバックし、その旨を通知する。
            force_offline = (
                self.settings.get("medical_templates_offline_only", True)
                and self.llm.is_medical_template()
                and self.llm.is_online()
            )
            if force_offline:
                self._ui(lambda: rumps.alert(
                    title="医療テンプレート — 外部送信をブロックしました",
                    message=(
                        "医療テンプレート使用中はオンラインAPIへの送信を禁止しています。\n"
                        "患者情報を外部に送らないよう、ローカル（LM Studio）で処理します。\n\n"
                        "この動作は設定 medical_templates_offline_only で変更できます。"
                    ),
                ))

            llm_error_msg = None
            try:
                processed = self.llm.process(raw_text, force_offline=force_offline)
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

            # 医療テンプレートは幻覚・誤転写がそのままカルテに入らないよう、
            # 挿入前に確認ダイアログを出す（M-1, M-4）。ユーザーは結果を修正でき、
            # 「挿入 / コピーのみ / 破棄」を選べる。
            if (self.settings.get("medical_confirm_before_insert", True)
                    and self.llm.is_medical_template()):
                app_name = self._frontmost_app_name()
                decision, edited = self._ask_on_main(
                    lambda: self._confirm_medical_insert(output, raw_text, app_name)
                )
                output = edited if edited is not None else output
            else:
                decision = "insert"

            if decision == "discard":
                notify("破棄しました", "結果は挿入されませんでした")
            elif decision == "copy":
                self._last_result = output
                self.inserter.copy(output)
                notify(
                    "クリップボードにコピーしました",
                    self._preview(output, 100) or f"{len(output)}文字",
                )
            else:  # insert
                self._last_result = output
                self.inserter.insert(output)
                notify("完了", self._preview(processed, 100) or f"{len(processed)}文字")

            if decision != "discard" and self.settings.get("save_sessions", False):
                self._save_session(audio_path, raw_text, processed)

        except Exception as e:
            notify("エラー", str(e)[:120])
        finally:
            # Data minimization: unless the user opted in, remove the recording
            # so patient audio does not linger on disk.
            if not self.settings.get("save_audio", False):
                try:
                    audio_path.unlink(missing_ok=True)
                except Exception:
                    pass
            with self._state_lock:
                self._is_processing = False
            self._ui(lambda: setattr(self, "title", self._idle_title()))

    # ------------------------------------------------------------------
    # Menu callbacks
    # ------------------------------------------------------------------

    def copy_last_result(self, sender):
        if self._last_result:
            self.inserter.copy(self._last_result)
            notify(
                "コピーしました",
                self._preview(self._last_result) or f"{len(self._last_result)}文字",
            )
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

        if new_mode == "online":
            # Warn that content leaves the Mac before enabling online mode.
            url = self.settings.get("online_api_url", "https://api.openai.com/v1")
            confirmed = rumps.alert(
                title="オンラインモードに切り替えますか？",
                message=(
                    f"文字起こし内容が外部API（{url}）に送信されます。\n"
                    "患者情報を含む音声には使用しないでください。\n\n"
                    "※ 医療テンプレート使用時は自動的にローカル処理へフォールバックします。"
                ),
                ok="オンラインにする",
                cancel="キャンセル",
            )
            if not confirmed:  # cancel returns 0
                return
            if not self.settings.get("online_api_key", "").strip():
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

    @staticmethod
    def _valid_online_url(url: str) -> bool:
        """Require https for online APIs; allow http only for localhost."""
        try:
            p = urlparse(url)
        except Exception:
            return False
        if p.scheme == "https":
            return True
        if p.scheme == "http" and p.hostname in ("localhost", "127.0.0.1", "::1"):
            return True
        return False

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
        if not self._valid_online_url(api_url):
            rumps.alert(
                title="URLエラー",
                message=(
                    "オンラインAPIのURLは https:// で指定してください。\n"
                    "（http:// は localhost のみ許可されます）"
                ),
            )
            return False

        # 2. API Key (stored in the macOS Keychain, never shown or prefilled)
        has_key = bool(self.settings.get("online_api_key", "").strip())
        key_msg = "APIキーを入力してください。\n(OpenAI: sk-...  /  Anthropic: sk-ant-...  など)"
        if has_key:
            key_msg = (
                "APIキーは設定済みです（Keychainに保存）。\n"
                "変更する場合のみ新しいキーを入力してください。\n"
                "空欄のまま「次へ」で現在のキーを維持します。"
            )
        win = rumps.Window(
            message=key_msg,
            title="オンライン設定 (2/3) — APIキー",
            default_text="",  # never prefill the secret into the dialog
            ok="次へ",
            cancel="キャンセル",
            dimensions=(420, 30),
        )
        r = win.run()
        if not r.clicked:
            return False
        # Keep the existing key when the field is left blank.
        api_key = r.text.strip() or self.settings.get("online_api_key", "")

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
        sessions_dir = secure_dir(BASE_DIR / "data" / "sessions")
        ts = datetime.now()
        keep_audio = self.settings.get("save_audio", False)
        record = {
            "timestamp":      ts.isoformat(),
            "audio_path":     str(audio_path) if keep_audio else "",
            "raw_text":       raw_text,
            "processed_text": processed,
            "template":       self.settings.get("active_template", "memo"),
        }
        out = sessions_dir / f"{ts.strftime('%Y%m%d_%H%M%S')}.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        harden(out)  # contains transcription/AI text — owner-only

    # ------------------------------------------------------------------
    # Data retention
    # ------------------------------------------------------------------

    def _purge_old_data(self):
        """On startup, delete recordings/sessions older than retention_days."""
        days = int(self.settings.get("retention_days", 7))
        if days <= 0:
            return
        cutoff = time.time() - days * 86400
        for sub in ("data/audio", "data/sessions"):
            d = BASE_DIR / sub
            if not d.exists():
                continue
            for f in d.iterdir():
                try:
                    if f.is_file() and f.stat().st_mtime < cutoff:
                        f.unlink()
                except Exception:
                    pass

    def purge_all_data(self, sender):
        resp = rumps.alert(
            title="保存データを全削除しますか？",
            message=(
                "録音音声とセッション記録（文字起こし・整形結果）を"
                "すべて削除します。元に戻せません。"
            ),
            ok="削除する",
            cancel="キャンセル",
        )
        if not resp:  # cancel
            return
        count = 0
        for sub in ("data/audio", "data/sessions"):
            d = BASE_DIR / sub
            if not d.exists():
                continue
            for f in d.iterdir():
                try:
                    if f.is_file():
                        f.unlink()
                        count += 1
                except Exception:
                    pass
        notify("保存データを削除しました", f"{count} 件")


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

if __name__ == "__main__":
    for d in ["data/audio", "data/sessions"]:
        secure_dir(BASE_DIR / d)
    (BASE_DIR / "models").mkdir(parents=True, exist_ok=True)

    VoiceMemoApp().run()
