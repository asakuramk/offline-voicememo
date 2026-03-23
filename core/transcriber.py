"""
Local speech-to-text using faster-whisper.
Models are downloaded on first use to the local models/ directory.
"""
from __future__ import annotations

from typing import Optional
from faster_whisper import WhisperModel
from pathlib import Path


class Transcriber:
    def __init__(self, settings: dict):
        self._settings = settings
        self._model: Optional[WhisperModel] = None
        self._loaded_model_size: Optional[str] = None

    def transcribe(self, audio_path: Path) -> str:
        model = self._load_model()
        language = self._settings.get("whisper_language", "ja")
        if language == "auto":
            language = None

        segments, _ = model.transcribe(
            str(audio_path),
            language=language,
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )
        return " ".join(seg.text.strip() for seg in segments).strip()

    def update_settings(self, settings: dict):
        self._settings = settings
        self._model = None  # force reload on next call

    def _load_model(self) -> "WhisperModel":
        model_size = self._settings.get("whisper_model", "small")
        device = self._settings.get("whisper_device", "cpu")
        compute_type = "int8" if device == "cpu" else "float16"
        models_dir = Path(__file__).parent.parent / "models"

        if self._model is None or self._loaded_model_size != model_size:
            self._model = WhisperModel(
                model_size,
                device=device,
                compute_type=compute_type,
                download_root=str(models_dir),
            )
            self._loaded_model_size = model_size

        return self._model
