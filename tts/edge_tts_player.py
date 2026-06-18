"""Edge TTS 英文语音合成。"""

from __future__ import annotations

import asyncio
import io
import logging
import threading

import edge_tts
import miniaudio
import numpy as np

logger = logging.getLogger(__name__)


class EdgeTTSPlayer:
    def __init__(self, voice: str = "en-US-GuyNeural", rate: str = "+0%") -> None:
        self._voice = voice
        self._rate = rate
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            self._loop_thread = threading.Thread(
                target=self._loop.run_forever, daemon=True
            )
            self._loop_thread.start()
        return self._loop

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        """将文本合成为 float32 mono 音频，返回 (audio, sample_rate)。"""
        if not text.strip():
            return np.array([], dtype=np.float32), 24000

        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(
            self._synthesize_async(text), loop
        )
        return future.result(timeout=60)

    async def _synthesize_async(self, text: str) -> tuple[np.ndarray, int]:
        communicate = edge_tts.Communicate(text, self._voice, rate=self._rate)
        mp3_buffer = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                mp3_buffer.write(chunk["data"])
        return _mp3_to_numpy(mp3_buffer.getvalue())


def _mp3_to_numpy(mp3_data: bytes) -> tuple[np.ndarray, int]:
    decoded = miniaudio.decode(mp3_data)
    samples = np.array(decoded.samples, dtype=np.float32)
    if decoded.sample_width == 2:
        samples /= 32768.0
    elif decoded.sample_width == 4:
        samples /= 2147483648.0
    sr = decoded.sample_rate
    if decoded.nchannels > 1:
        samples = samples.reshape(-1, decoded.nchannels).mean(axis=1)
    return samples.astype(np.float32), sr
