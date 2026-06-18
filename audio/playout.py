"""TTS 音频播放到虚拟麦克风。"""

from __future__ import annotations

from collections.abc import Callable
import logging
import threading

import numpy as np
import sounddevice as sd

logger = logging.getLogger(__name__)


class AudioPlayout:
    """将 float32 mono 音频播放到指定输出设备（VB-Cable Input）。"""

    def __init__(
        self,
        device_index: int | None,
        sample_rate: int = 24000,
    ) -> None:
        self._device_index = device_index
        self._sample_rate = sample_rate
        self._lock = threading.Lock()
        self._playing = False

    @property
    def device_index(self) -> int | None:
        return self._device_index

    @property
    def is_playing(self) -> bool:
        return self._playing

    def play(self, audio: np.ndarray, sample_rate: int | None = None) -> None:
        """阻塞播放直到完成。"""
        if len(audio) == 0:
            return
        sr = sample_rate or self._sample_rate
        with self._lock:
            self._playing = True
        try:
            sd.play(
                audio,
                samplerate=sr,
                device=self._device_index,
                blocking=True,
            )
        finally:
            with self._lock:
                self._playing = False

    def play_async(
        self,
        audio: np.ndarray,
        sample_rate: int | None = None,
        on_done: Callable[[], None] | None = None,
    ) -> None:
        def _run() -> None:
            self.play(audio, sample_rate)
            if on_done:
                on_done()

        threading.Thread(target=_run, daemon=True).start()

    def play_dual(
        self,
        audio: np.ndarray,
        sample_rate: int,
        monitor_device_index: int | None,
    ) -> None:
        """同时播放到虚拟麦与监听设备（内容相同）。"""
        if len(audio) == 0:
            return
        with self._lock:
            self._playing = True
        try:
            threads: list[threading.Thread] = []

            def _to_device(device_index: int | None) -> None:
                sd.play(
                    audio,
                    samplerate=sample_rate,
                    device=device_index,
                    blocking=True,
                )

            threads.append(
                threading.Thread(
                    target=_to_device, args=(self._device_index,), daemon=True
                )
            )
            threads.append(
                threading.Thread(
                    target=_to_device, args=(monitor_device_index,), daemon=True
                )
            )
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        finally:
            with self._lock:
                self._playing = False

    def play_to_device(
        self,
        audio: np.ndarray,
        sample_rate: int,
        device_index: int | None,
    ) -> None:
        """播放到指定输出设备。"""
        if len(audio) == 0:
            return
        with self._lock:
            self._playing = True
        try:
            sd.play(
                audio,
                samplerate=sample_rate,
                device=device_index,
                blocking=True,
            )
        finally:
            with self._lock:
                self._playing = False
