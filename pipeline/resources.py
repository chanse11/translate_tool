"""应用启动时预加载的共享模型与引擎。"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from asr.whisper_asr import WhisperASR
from translate.argos_translator import ArgosTranslator

logger = logging.getLogger(__name__)


class AppResources:
    """Whisper（英/中）与 Argos 翻译，全局只加载一次。"""

    def __init__(self, config: dict) -> None:
        whisper_cfg = config.get("whisper", {})
        beam_size = whisper_cfg.get("beam_size", 1)
        self.en_asr = WhisperASR(
            model_size=whisper_cfg.get("english_model", "small.en"),
            device=whisper_cfg.get("device", "cpu"),
            compute_type=whisper_cfg.get("compute_type", "int8"),
            beam_size=beam_size,
        )
        self.zh_asr = WhisperASR(
            model_size=whisper_cfg.get("chinese_model", "small"),
            device=whisper_cfg.get("device", "cpu"),
            compute_type=whisper_cfg.get("compute_type", "int8"),
            beam_size=beam_size,
        )
        self.translator = ArgosTranslator()
        self._ready = False
        self._lock = threading.Lock()
        self._error: str | None = None

    @property
    def is_ready(self) -> bool:
        return self._ready

    @property
    def error(self) -> str | None:
        return self._error

    def preload(self, on_progress: Callable[[str], None] | None = None) -> None:
        with self._lock:
            if self._ready:
                return
            self._error = None

        def progress(msg: str) -> None:
            logger.info("预加载: %s", msg)
            if on_progress:
                on_progress(msg)

        try:
            progress("正在加载英语识别模型 (Whisper)…")
            self.en_asr.load()
            progress("正在加载中文识别模型 (Whisper)…")
            self.zh_asr.load()
            progress("正在加载离线翻译语言包 (Argos)…")
            self.translator.setup()
            progress("正在预热语音合成 (Edge TTS)…")
            self._warmup_tts()
            with self._lock:
                self._ready = True
            progress("全部就绪")
            logger.info("预加载完成")
        except Exception as exc:
            with self._lock:
                self._error = str(exc)
            logger.exception("预加载失败")
            raise

    def _warmup_tts(self) -> None:
        """轻量预热 Edge TTS 事件循环（需联网，失败不影响 ASR/翻译）。"""
        try:
            from tts.edge_tts_player import EdgeTTSPlayer

            EdgeTTSPlayer()._ensure_loop()
        except Exception as exc:
            logger.warning("TTS 预热跳过: %s", exc)
