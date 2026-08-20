"""应用启动时预加载的共享模型与引擎。"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)


class AppResources:
    """Whisper（英/中）与 Argos 翻译，仅在 preload() 时真正加载。"""

    def __init__(self, config: dict) -> None:
        self._config = config
        self._whisper_cfg = config.get("whisper", {})
        self.en_asr = None
        self.zh_asr = None
        self.translator = None
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
            progress("正在加载语音识别与翻译组件…")
            from asr.whisper_asr import WhisperASR
            from translate.argos_translator import ArgosTranslator

            beam_size = self._whisper_cfg.get("beam_size", 1)
            self.en_asr = WhisperASR(
                model_size=self._whisper_cfg.get("english_model", "small.en"),
                device=self._whisper_cfg.get("device", "cpu"),
                compute_type=self._whisper_cfg.get("compute_type", "int8"),
                beam_size=beam_size,
            )
            self.zh_asr = WhisperASR(
                model_size=self._whisper_cfg.get("chinese_model", "small"),
                device=self._whisper_cfg.get("device", "cpu"),
                compute_type=self._whisper_cfg.get("compute_type", "int8"),
                beam_size=beam_size,
            )
            self.translator = ArgosTranslator()

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
        try:
            from tts.edge_tts_player import EdgeTTSPlayer

            EdgeTTSPlayer()._ensure_loop()
        except Exception as exc:
            logger.warning("TTS 预热跳过: %s", exc)
