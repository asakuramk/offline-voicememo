"""
LLM client using OpenAI-compatible API provided by LM Studio.
Applies a prompt template to the raw transcription and returns structured text.
"""
from __future__ import annotations

from typing import Optional, Dict
from openai import OpenAI, APIConnectionError
from pathlib import Path

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

BUILTIN_TEMPLATES: Dict[str, str] = {
    "memo": (
        "以下の音声文字起こしを、読みやすいメモとして整形してください。\n"
        "言い淀み（えー、あの、うーんなど）や繰り返しは除去し、"
        "内容は変えずに箇条書きや段落を使って整理してください。\n\n"
        "# 文字起こし\n{text}"
    ),
    "minutes": (
        "以下の音声文字起こしを議事録形式に整形してください。\n"
        "「議題」「決定事項」「TODO」のセクションに分けてMarkdown形式で出力してください。\n\n"
        "# 文字起こし\n{text}"
    ),
    "tasks": (
        "以下の音声文字起こしから、タスクやToDoを抽出して"
        "Markdownのチェックリスト形式（- [ ] タスク）で出力してください。\n\n"
        "# 文字起こし\n{text}"
    ),
    "journal": (
        "以下の音声文字起こしを、日記・ジャーナルエントリーとして自然な文章に整形してください。\n"
        "一人称で、読みやすい段落に整えてください。\n\n"
        "# 文字起こし\n{text}"
    ),
    "summary": (
        "以下の音声文字起こしを3〜5行で要約してください。重要なポイントのみ簡潔に。\n\n"
        "# 文字起こし\n{text}"
    ),
    "raw": "{text}",
}


class LLMClient:
    def __init__(self, settings: dict):
        self._settings = settings
        self._client: Optional[OpenAI] = None

    def process(self, raw_text: str) -> str:
        if not raw_text.strip():
            return ""

        template_name = self._settings.get("active_template", "memo")
        if template_name == "raw":
            return raw_text

        prompt = self._build_prompt(template_name, raw_text)
        client = self._get_client()
        model = self._resolve_model(client)

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": "あなたは日本語の音声文字起こしを整形するアシスタントです。指示通りに整形し、余計な説明は不要です。",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=float(self._settings.get("lmstudio_temperature", 0.3)),
                max_tokens=int(self._settings.get("lmstudio_max_tokens", 2048)),
            )
        except APIConnectionError:
            if self.is_online():
                url = self._settings.get("online_api_url", "https://api.openai.com/v1")
                raise RuntimeError(
                    f"オンラインAPIに接続できません ({url})\n"
                    "APIキーとURLをメニュー「オンライン設定」で確認してください。"
                )
            else:
                url = self._settings.get("lmstudio_url", "http://localhost:1234/v1")
                raise RuntimeError(
                    f"LM Studio に接続できません ({url})\n"
                    "LM Studio を起動してモデルをロードし、Local Server を開始してください。"
                )
        return response.choices[0].message.content.strip()

    def update_settings(self, settings: dict):
        self._settings = settings
        self._client = None  # force reconnect on next call

    def is_online(self) -> bool:
        return self._settings.get("llm_mode", "offline") == "online"

    def _get_client(self) -> "OpenAI":
        if self._client is None:
            if self.is_online():
                base_url = self._settings.get("online_api_url", "https://api.openai.com/v1")
                api_key  = self._settings.get("online_api_key", "")
            else:
                base_url = self._settings.get("lmstudio_url", "http://localhost:1234/v1")
                api_key  = "lm-studio"
            self._client = OpenAI(base_url=base_url, api_key=api_key or "no-key")
        return self._client

    def _resolve_model(self, client: "OpenAI") -> str:
        """Return model name for the current mode."""
        if self.is_online():
            return self._settings.get("online_model", "gpt-4o-mini")

        # Offline: use configured name or auto-detect from LM Studio
        configured = self._settings.get("lmstudio_model", "local-model")
        if configured and configured != "local-model":
            return configured
        try:
            models = client.models.list()
            if models.data:
                return models.data[0].id
        except APIConnectionError:
            url = self._settings.get("lmstudio_url", "http://localhost:1234/v1")
            raise RuntimeError(
                f"LM Studio に接続できません ({url})\n"
                "LM Studio を起動してモデルをロードし、Local Server を開始してください。"
            )
        except Exception:
            pass
        return configured

    def _build_prompt(self, template_name: str, text: str) -> str:
        return self.get_template_content(template_name).replace("{text}", text)

    # ------------------------------------------------------------------
    # Template file helpers (used by the editor UI in main.py)
    # ------------------------------------------------------------------

    def get_template_content(self, key: str) -> str:
        """Return editable template content: .txt file if exists, else builtin."""
        path = TEMPLATES_DIR / f"{key}.txt"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return BUILTIN_TEMPLATES.get(key, BUILTIN_TEMPLATES["summary"])

    def save_template_content(self, key: str, content: str):
        """Write content to templates/<key>.txt."""
        TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
        (TEMPLATES_DIR / f"{key}.txt").write_text(content, encoding="utf-8")
