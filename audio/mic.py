"""物理麦克风 PTT 录音。"""

from __future__ import annotations

import logging
import threading

import numpy as np
import sounddevice as sd

logger = logging.getLogger(__name__)


class MicRecorder:
    """按住说话时录音，松手后返回完整音频。"""

    def __init__(
        self,
        device_index: int | None,
        sample_rate: int = 16000,
        channels: int = 1,
    ) -> None:
        self._device_index = device_index
        self._sample_rate = sample_rate
        self._channels = channels
        self._frames: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()
        self._recording = False

    @property
    def is_recording(self) -> bool:
        return self._recording

    def start(self) -> None:
        with self._lock:
            if self._recording:
                return
            self._frames.clear()
            self._recording = True
            self._stream = sd.InputStream(
                samplerate=self._sample_rate,
                channels=self._channels,
                dtype="float32",
                device=self._device_index,
                blocksize=int(self._sample_rate * 0.03) or 480,
                callback=self._callback,
            )
            self._stream.start()
        try:
            info = sd.query_devices(self._device_index)
            name = info.get("name", "?") if isinstance(info, dict) else str(info)
        except Exception:
            name = "?"
        logger.info("麦克风录音开始 (device=%s name=%s)", self._device_index, name)

    def stop(self) -> np.ndarray:
        stream = None
        with self._lock:
            if not self._recording:
                return np.array([], dtype=np.float32)
            self._recording = False
            stream = self._stream
            self._stream = None
        # 先停流再取锁拼帧，避免与 callback 死锁
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                logger.debug("关闭麦克风流失败", exc_info=True)
        with self._lock:
            if not self._frames:
                return np.array([], dtype=np.float32)
            audio = np.concatenate(self._frames)
        logger.info("麦克风录音结束, %d samples (%.2fs)", len(audio), len(audio) / self._sample_rate)
        return audio

    def _callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: object,
        status: sd.CallbackFlags,
    ) -> None:
        if status:
            logger.warning("麦克风状态: %s", status)
        with self._lock:
            if self._recording:
                mono = indata[:, 0] if indata.ndim > 1 else indata.flatten()
                self._frames.append(mono.copy())
