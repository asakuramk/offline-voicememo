"""
User-defined replacement dictionary applied after Whisper transcription.
Stored in config/dictionary.json as {"誤認識": "正しい表記", ...}.

Editor format (shown in the rumps.Window dialog):
    誤認識 = 正しい表記
    (one entry per line; lines starting with # are comments; blank lines ignored)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

DICT_PATH = Path(__file__).parent.parent / "config" / "dictionary.json"

EDITOR_HEADER = """\
# 変換辞書  — 「変換前 = 変換後」の形式で1行ずつ記入してください。
# 例:
#   LM スタジオ = LM Studio
#   エルエム = LM
#   てんかん = 癲癇
#   ひくつ = 被虐
# ※ # で始まる行はコメント、空行は無視されます。
"""


class Dictionary:
    def __init__(self):
        self._entries: dict[str, str] = {}
        self.load()

    def load(self):
        if DICT_PATH.exists():
            try:
                self._entries = json.loads(DICT_PATH.read_text(encoding="utf-8"))
            except Exception:
                self._entries = {}
        else:
            self._entries = {}

    def apply(self, text: str) -> str:
        """Replace all dictionary entries in text (longest match first)."""
        if not self._entries:
            return text
        for src, dst in sorted(self._entries.items(), key=lambda x: -len(x[0])):
            text = text.replace(src, dst)
        return text

    # ------------------------------------------------------------------
    # Editor helpers
    # ------------------------------------------------------------------

    def to_editor_text(self) -> str:
        """Serialize current entries to the human-editable format."""
        lines = [EDITOR_HEADER]
        for src, dst in self._entries.items():
            lines.append(f"{src} = {dst}")
        return "\n".join(lines)

    def from_editor_text(self, text: str):
        """Parse editor text and save to dictionary.json."""
        entries: dict[str, str] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            left, _, right = line.partition("=")
            src = left.strip()
            dst = right.strip()
            if src:
                entries[src] = dst
        self._entries = entries
        DICT_PATH.parent.mkdir(parents=True, exist_ok=True)
        DICT_PATH.write_text(
            json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
        )
