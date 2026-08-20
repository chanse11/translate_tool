"""麦克风持续采集，供 VAD 断句使用。"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

import numpy as np
import sounddevice as sd

logger = logging.getLogger(__name__)


class MicStreamCapture:
    """持续从麦克风采集音频，回调返回 float32 mono 块。"""

    def __init__(
        self,
        device_index: int | None,
        sample_rate: int = 16000,
        chunk_ms: int = 30,
        on_audio: Callable[[np.ndarray], None] | None = None,
    ) -> None:
        self._device_index = device_index
        self._sample_rate = sample_rate
        self._chunk_samples = int(sample_rate * chunk_ms / 1000)
        self._on_audio = on_audio
        self._stream: sd.InputStream | None = None
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stream = sd.InputStream(
            samplerate=self._sample_rate,
            channels=1,
            dtype="float32",
            device=self._device_index,
            blocksize=self._chunk_samples,
            callback=self._callback,
        )
        self._stream.start()
        try:
            info = sd.query_devices(self._device_index)
            name = info.get("name", "?") if isinstance(info, dict) else str(info)
        except Exception:
            name = "?"
        logger.info(
            "麦克风持续采集已启动 (device=%s name=%s)",
            self._device_index,
            name,
        )

    def stop(self) -> None:
        self._running = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        logger.debug("麦克风持续采集已停止")

    def _callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: object,
        status: sd.CallbackFlags,
    ) -> None:
        if status:
            logger.warning("麦克风状态: %s", status)
        if not self._running or self._on_audio is None:
            return
        mono = indata[:, 0] if indata.ndim > 1 else indata.flatten()
        self._on_audio(mono.copy())
