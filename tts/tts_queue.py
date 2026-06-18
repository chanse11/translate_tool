"""分句 TTS 队列：逐句合成并顺序播放，降低首句延迟。"""

from __future__ import annotations

import logging
import threading

import numpy as np

from audio.playout import AudioPlayout
from tts.edge_tts_player import EdgeTTSPlayer
from tts.text_split import split_for_tts

logger = logging.getLogger(__name__)


class TTSQueue:
    def __init__(
        self,
        tts: EdgeTTSPlayer,
        playout: AudioPlayout,
        monitor_index: int | None = None,
        test_mode: bool = False,
    ) -> None:
        self._tts = tts
        self._playout = playout
        self._monitor_index = monitor_index
        self._test_mode = test_mode
        self._lock = threading.Lock()
        self._playing = False
        self._cancel = threading.Event()

    @property
    def is_playing(self) -> bool:
        return self._playing

    def set_test_mode(self, enabled: bool) -> None:
        self._test_mode = enabled

    def cancel(self) -> None:
        self._cancel.set()

    def play_text(self, text: str, chunked: bool = True) -> None:
        """阻塞播放文本（可在后台线程调用）。"""
        sentences = split_for_tts(text) if chunked else [text.strip()]
        if not sentences:
            return

        with self._lock:
            self._playing = True
            self._cancel.clear()

        try:
            for sentence in sentences:
                if self._cancel.is_set():
                    break
                logger.debug("TTS 队列播放: %s", sentence[:60])
                tts_audio, tts_sr = self._tts.synthesize(sentence)
                if self._cancel.is_set():
                    break
                self._play_chunk(tts_audio, tts_sr)
        finally:
            with self._lock:
                self._playing = False

    def play_text_async(
        self,
        text: str,
        chunked: bool = True,
        on_start: callable | None = None,
        on_end: callable | None = None,
    ) -> None:
        def _run() -> None:
            if on_start:
                on_start()
            try:
                self.play_text(text, chunked=chunked)
            finally:
                if on_end:
                    on_end()

        threading.Thread(target=_run, daemon=True).start()

    def _play_chunk(self, audio: np.ndarray, sample_rate: int) -> None:
        if len(audio) == 0:
            return
        if self._test_mode and self._monitor_index is not None:
            self._playout.play_dual(audio, sample_rate, self._monitor_index)
        else:
            self._playout.play(audio, sample_rate)
