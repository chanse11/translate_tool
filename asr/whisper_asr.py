"""faster-whisper 本地语音识别封装。"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)


class WhisperASR:
    def __init__(
        self,
        model_size: str = "small.en",
        device: str = "cpu",
        compute_type: str = "int8",
        download_root: str | Path | None = None,
        beam_size: int = 1,
    ) -> None:
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._download_root = str(download_root) if download_root else None
        self._beam_size = beam_size
        self._model: WhisperModel | None = None

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        if self._model is not None:
            return
        logger.info(
            "加载 Whisper 模型: %s (device=%s, compute=%s)",
            self._model_size,
            self._device,
            self._compute_type,
        )
        self._model = WhisperModel(
            self._model_size,
            device=self._device,
            compute_type=self._compute_type,
            download_root=self._download_root,
        )

    def transcribe(
        self,
        audio: np.ndarray,
        language: str | None = None,
        sample_rate: int = 16000,
    ) -> str:
        if self._model is None:
            self.load()
        if len(audio) == 0:
            return ""
        audio = audio.astype(np.float32)
        peak = np.max(np.abs(audio))
        if peak > 0:
            audio = audio / peak
        segments, _info = self._model.transcribe(
            audio,
            language=language,
            beam_size=self._beam_size,
            vad_filter=False,
            condition_on_previous_text=False,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        logger.debug("ASR [%s]: %s", language or "auto", text)
        return text
