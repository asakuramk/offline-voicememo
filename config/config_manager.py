"""
Loads and saves settings.json with defaults applied for missing keys.
"""
import json
from pathlib import Path

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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
