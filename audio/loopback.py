"""WASAPI Loopback 采集 Zoom 播放音频。"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

import numpy as np
import pyaudiowpatch as pyaudio

logger = logging.getLogger(__name__)


class LoopbackCapture:
    """持续从环回设备采集音频，回调返回 float32 mono 16kHz 块。"""

    def __init__(
        self,
        device_info: dict,
        target_sample_rate: int = 16000,
        chunk_ms: int = 30,
        on_audio: Callable[[np.ndarray], None] | None = None,
    ) -> None:
        self._device_info = device_info
        self._target_sr = target_sample_rate
        self._chunk_ms = chunk_ms
        self._on_audio = on_audio
        self._pa: pyaudio.PyAudio | None = None
        self._stream: pyaudio.Stream | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._device_sr = int(device_info["defaultSampleRate"])
        self._channels = device_info["maxInputChannels"]

    @property
    def device_sample_rate(self) -> int:
        return self._device_sr

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None

    def _run(self) -> None:
        self._pa = pyaudio.PyAudio()
        frames_per_buffer = int(self._device_sr * self._chunk_ms / 1000)
        try:
            self._stream = self._pa.open(
                format=pyaudio.paFloat32,
                channels=self._channels,
                rate=self._device_sr,
                frames_per_buffer=frames_per_buffer,
                input=True,
                input_device_index=self._device_info["index"],
            )
            logger.info(
                "环回采集已启动: %s (%d Hz, %d ch)",
                self._device_info["name"],
                self._device_sr,
                self._channels,
            )
            while not self._stop_event.is_set():
                raw = self._stream.read(
                    frames_per_buffer, exception_on_overflow=False
                )
                samples = np.frombuffer(raw, dtype=np.float32)
                if self._channels > 1:
                    samples = samples.reshape(-1, self._channels).mean(axis=1)
                if self._device_sr != self._target_sr:
                    samples = _resample(samples, self._device_sr, self._target_sr)
                if self._on_audio is not None:
                    self._on_audio(samples)
        except Exception as exc:
            logger.exception("环回采集异常: %s", exc)
        finally:
            if self._stream:
                self._stream.stop_stream()
                self._stream.close()
            if self._pa:
                self._pa.terminate()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


def _resample(samples: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    if src_sr == dst_sr or len(samples) == 0:
        return samples
    duration = len(samples) / src_sr
    dst_len = int(duration * dst_sr)
    if dst_len <= 0:
        return np.array([], dtype=np.float32)
    x_old = np.linspace(0, 1, len(samples), endpoint=False)
    x_new = np.linspace(0, 1, dst_len, endpoint=False)
    return np.interp(x_new, x_old, samples).astype(np.float32)
