"""
Loads and saves settings.json with defaults applied for missing keys.
"""
import json
from pathlib import Path

from core.secure_fs import harden, secure_dir

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

    def load(self) -> dict:
        config = DEFAULTS.copy()
        if self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                config.update(json.load(f))
        else:
            self.save(config)
        return config

    def save(self, config: dict):
        secure_dir(self.path.parent)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        harden(self.path)  # settings may hold an API key — owner-only
