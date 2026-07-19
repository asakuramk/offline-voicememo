"""
Loads and saves settings.json with defaults applied for missing keys.

The online API key is never written to settings.json; it is stored in the
macOS Keychain via `keyring`. A plaintext key found in an older settings.json
is migrated to the Keychain on first load and stripped from the file.
"""
import json
from pathlib import Path

from core.secure_fs import harden, secure_dir

try:
    import keyring
except Exception:  # pragma: no cover - keyring may be missing in minimal envs
    keyring = None

KEYCHAIN_SERVICE = "offline-voicememo"
KEYCHAIN_ACCOUNT = "online_api_key"

DEFAULTS: dict = {
    "hotkey": "alt",
    "whisper_model": "small",
    "whisper_language": "ja",
    "whisper_device": "cpu",
    # --- Offline (LM Studio) ---
    "lmstudio_url": "http://localhost:1234/v1",
    "lmstudio_model": "local-model",
    "lmstudio_temperature": 0.3,
    "lmstudio_max_tokens": 2048,
    # --- Online API ---
    "llm_mode": "offline",          # "offline" | "online"
    "online_api_url": "https://api.openai.com/v1",
    "online_api_key": "",
    "online_model": "gpt-4o-mini",
    # ---
    "active_template": "summary",
    "restore_clipboard": True,
    "show_raw_text": False,   # prepend raw transcription to output
    # --- Privacy / medical safety ---
    # 医療テンプレート（問診/SOAP/医療サマリー）選択中は外部APIへの送信を禁止し、
    # 常にオフライン（ローカル）処理へフォールバックする。
    "medical_templates_offline_only": True,
    # macOS通知に文字起こし内容のプレビューを含めるか（既定は含めない）。
    "notify_content_preview": False,
    # --- Data retention ---
    # 録音音声・セッション記録を端末に残すか（既定は残さない＝データ最小化）。
    "save_audio": False,
    "save_sessions": False,
    # 保存を有効にした場合の保持日数。起動時にこれを過ぎたデータを自動削除する。
    "retention_days": 7,
}


class ConfigManager:
    def __init__(self, path: Path):
        self.path = path

    # ------------------------------------------------------------------
    # Keychain-backed API key
    # ------------------------------------------------------------------

    def _get_api_key(self) -> str:
        if keyring is None:
            return ""
        try:
            return keyring.get_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT) or ""
        except Exception:
            return ""

    def _set_api_key(self, value: str):
        if keyring is None:
            return
        try:
            if value:
                keyring.set_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT, value)
            else:
                try:
                    keyring.delete_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
                except Exception:
                    pass
        except Exception:
            pass

    # ------------------------------------------------------------------

    def load(self) -> dict:
        config = DEFAULTS.copy()
        migrated = False
        if self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
            config.update(raw)
            legacy = (raw.get("online_api_key") or "").strip()
            if legacy:
                # Move a plaintext key out of settings.json into the Keychain.
                self._set_api_key(legacy)
                migrated = True
        else:
            self.save(config)

        # The live key always comes from the Keychain, never from disk.
        config["online_api_key"] = self._get_api_key()
        if migrated:
            self.save(config)  # rewrite settings.json without the plaintext key
        return config

    def save(self, config: dict):
        # Store the API key in the Keychain; keep it out of settings.json.
        self._set_api_key(config.get("online_api_key", ""))
        to_write = dict(config)
        to_write["online_api_key"] = ""

        secure_dir(self.path.parent)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(to_write, f, ensure_ascii=False, indent=2)
        harden(self.path)  # owner-only regardless
