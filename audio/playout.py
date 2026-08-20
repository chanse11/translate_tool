"""TTS 音频播放到虚拟麦克风。"""

from __future__ import annotations

from collections.abc import Callable
import logging
import queue
import threading
import time

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


class StreamingAudioPlayout:
    """把 PCM 分片持续写入输出设备，实现边收边播（降低首音延迟）。

    写入走独立线程：`write_*` 只入队，不阻塞调用方。
    否则在 WebSocket 接收线程里同步 write() 会卡住事件循环，
    上一句 TTS 播放期间麦克风音频无法上送。
    """

    def __init__(
        self,
        device_index: int | None,
        sample_rate: int = 24000,
    ) -> None:
        self._device_index = device_index
        self._sample_rate = sample_rate
        self._lock = threading.Lock()
        self._stream: sd.OutputStream | None = None
        self._playing = False
        self._writing = False
        self._last_write_ts = 0.0
        self._active_sr = sample_rate
        self._queue: queue.Queue[tuple[int, np.ndarray] | None] = queue.Queue()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None

    @property
    def device_index(self) -> int | None:
        return self._device_index

    @property
    def is_playing(self) -> bool:
        if self._writing or self._queue.qsize() > 0:
            return True
        if self._last_write_ts <= 0:
            return False
        return (time.monotonic() - self._last_write_ts) < 0.35

    def write_pcm16(self, pcm16: bytes, sample_rate: int | None = None) -> None:
        if not pcm16:
            return
        audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        self.write_float32(audio, sample_rate)

    def write_float32(
        self, audio: np.ndarray, sample_rate: int | None = None
    ) -> None:
        if audio is None or len(audio) == 0:
            return
        sr = int(sample_rate or self._sample_rate)
        samples = np.asarray(audio, dtype=np.float32).reshape(-1).copy()
        self._ensure_worker()
        self._playing = True
        self._queue.put((sr, samples))

    def close(self) -> None:
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=2.0)
        self._worker = None
        with self._lock:
            self._close_stream_unlocked()
        self._playing = False
        self._writing = False
        self._drain_queue()

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._stop.clear()
            self._worker = threading.Thread(
                target=self._play_loop, daemon=True, name="stream-playout"
            )
            self._worker.start()

    def _play_loop(self) -> None:
        logger.debug(
            "StreamingAudioPlayout 播放线程已启动 device=%s", self._device_index
        )
        try:
            while not self._stop.is_set():
                try:
                    item = self._queue.get(timeout=0.15)
                except queue.Empty:
                    continue
                if item is None:
                    break
                sr, samples = item
                try:
                    self._write_blocking(sr, samples)
                except Exception:
                    logger.exception("StreamingAudioPlayout 写入失败，尝试重建流")
                    with self._lock:
                        self._close_stream_unlocked()
        finally:
            with self._lock:
                self._close_stream_unlocked()
            self._playing = False
            self._writing = False

    def _write_blocking(self, sr: int, samples: np.ndarray) -> None:
        with self._lock:
            if self._stream is None or self._active_sr != sr:
                self._close_stream_unlocked()
                self._active_sr = sr
                self._stream = sd.OutputStream(
                    samplerate=sr,
                    channels=1,
                    dtype="float32",
                    device=self._device_index,
                    blocksize=0,
                )
                self._stream.start()
                logger.debug(
                    "StreamingAudioPlayout 已启动 sr=%s device=%s",
                    sr,
                    self._device_index,
                )
            stream = self._stream
        self._writing = True
        try:
            stream.write(samples)
            self._last_write_ts = time.monotonic()
        finally:
            self._writing = False

    def _drain_queue(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def _close_stream_unlocked(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
            except Exception:
                pass
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None
