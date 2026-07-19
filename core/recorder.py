"""
Audio recorder using sounddevice.
Records microphone input as 16kHz mono WAV suitable for Whisper.
"""
import sounddevice as sd
import numpy as np
import scipy.io.wavfile as wavfile
from pathlib import Path
from datetime import datetime

from core.secure_fs import harden, secure_dir


class Recorder:
    SAMPLERATE = 16000
    CHANNELS = 1

    def __init__(self):
        self._frames: list = []
        self._recording = False
        self._stream = None

    def start(self):
        self._frames = []
        self._recording = True
        self._stream = sd.InputStream(
            samplerate=self.SAMPLERATE,
            channels=self.CHANNELS,
            dtype="float32",
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> Path:
        self._recording = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        audio_dir = secure_dir(Path(__file__).parent.parent / "data" / "audio")

        filename = datetime.now().strftime("%Y%m%d_%H%M%S") + ".wav"
        filepath = audio_dir / filename

        if self._frames:
            audio = np.concatenate(self._frames, axis=0).flatten()
            audio_int16 = (audio * 32767).astype(np.int16)
        else:
            audio_int16 = np.array([], dtype=np.int16)

        wavfile.write(str(filepath), self.SAMPLERATE, audio_int16)
        harden(filepath)  # recordings may contain patient audio — owner-only
        return filepath

    def _callback(self, indata, frames, time, status):
        if self._recording:
            self._frames.append(indata.copy())
