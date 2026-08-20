"""百炼英 → 中：环回采集 → LiveTranslate → 字幕（可选播中文）。"""

from __future__ import annotations

import logging
import threading

import numpy as np

from audio.devices import get_default_output_device, resolve_loopback_device
from audio.loopback import LoopbackCapture
from audio.playout import AudioPlayout
from bailian.livetranslate import BailianLiveTranslateSession, TurnResult
from pipeline.en_to_zh import SubtitleEntry

logger = logging.getLogger(__name__)


def pcm16_to_float32(pcm16: bytes) -> np.ndarray:
    if not pcm16:
        return np.zeros(0, dtype=np.float32)
    audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
    return audio


class BailianEnToZhPipeline:
    def __init__(
        self,
        config: dict,
        api_key: str,
        on_subtitle: callable,
        on_status: callable | None = None,
        is_paused: callable | None = None,
        tts_enabled: callable | None = None,
    ) -> None:
        self._config = config
        self._api_key = api_key
        audio_cfg = config.get("audio", {})
        bailian_cfg = config.get("bailian", {})

        self._sample_rate = audio_cfg.get("sample_rate", 16000)
        self._on_subtitle = on_subtitle
        self._on_status = on_status
        self._is_paused = is_paused or (lambda: False)
        self._tts_enabled = tts_enabled or (lambda: False)

        self._model = bailian_cfg.get("model", "qwen3.5-livetranslate-flash-realtime")
        self._ws_base = bailian_cfg.get("ws_base", "")
        self._workspace_id = bailian_cfg.get("workspace_id", "")
        self._region = bailian_cfg.get("region", "cn-beijing")

        self._monitor_index = get_default_output_device()
        self._playout = AudioPlayout(device_index=self._monitor_index, sample_rate=24000)

        self._session: BailianLiveTranslateSession | None = None
        self._capture: LoopbackCapture | None = None
        self._running = False
        self._tts_playing = False
        self._play_lock = threading.Lock()

    def update_credentials(self, api_key: str, config: dict | None = None) -> None:
        self._api_key = api_key
        if config is not None:
            self._config = config
            bailian_cfg = config.get("bailian", {})
            self._model = bailian_cfg.get("model", self._model)
            self._ws_base = bailian_cfg.get("ws_base", self._ws_base)
            self._workspace_id = bailian_cfg.get("workspace_id", self._workspace_id)
            self._region = bailian_cfg.get("region", self._region)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_tts_playing(self) -> bool:
        return self._tts_playing or self._playout.is_playing

    def start(self) -> None:
        if self._running:
            return
        if not self._api_key.strip():
            raise RuntimeError("请先填写阿里百炼 API Key")

        loopback_keyword = self._config.get("audio", {}).get("loopback_device", "")
        device = resolve_loopback_device(loopback_keyword)
        if device is None:
            raise RuntimeError("未找到环回音频设备，请检查音频设置。")

        # 启动前同步最新配置
        bailian_cfg = self._config.get("bailian", {})
        self._model = bailian_cfg.get("model", self._model)
        self._ws_base = bailian_cfg.get("ws_base", self._ws_base)
        self._workspace_id = bailian_cfg.get("workspace_id", self._workspace_id)
        self._region = bailian_cfg.get("region", self._region)
        silence_ms = int(bailian_cfg.get("silence_duration_ms", 1000) or 1000)
        vad_threshold = float(bailian_cfg.get("vad_threshold", 0.2) or 0.2)
        prefix_padding_ms = int(bailian_cfg.get("prefix_padding_ms", 500) or 500)

        self._session = BailianLiveTranslateSession(
            api_key=self._api_key,
            source_language="en",
            target_language="zh",
            model=self._model,
            ws_base=self._ws_base,
            workspace_id=self._workspace_id,
            region=self._region,
            audio_output=True,
            enable_source_asr=True,
            silence_duration_ms=silence_ms,
            vad_threshold=vad_threshold,
            prefix_padding_ms=prefix_padding_ms,
            on_partial=self._on_partial,
            on_turn=self._on_turn,
            on_status=self._set_status,
            on_error=self._set_status,
        )
        self._session.start()

        self._capture = LoopbackCapture(
            device_info=device,
            target_sample_rate=self._sample_rate,
            on_audio=self._on_audio_chunk,
        )
        self._running = True
        self._capture.start()
        self._set_status("百炼英→中流式监听中…")
        logger.info("Bailian EnToZh 流式模式已启动")

    def stop(self) -> None:
        self._running = False
        if self._capture:
            self._capture.stop()
            self._capture = None
        if self._session:
            self._session.stop()
            self._session = None
        self._set_status("英→中已停止")

    def _on_audio_chunk(self, chunk: np.ndarray) -> None:
        if not self._running or not self._session:
            return
        if self._is_paused():
            return
        self._session.send_float32(chunk)

    def _on_partial(self, source: str, translation: str) -> None:
        self._on_subtitle(
            SubtitleEntry(
                english=source or "…",
                chinese=translation or "…",
                partial=True,
            )
        )

    def _on_turn(self, turn: TurnResult) -> None:
        english = turn.source_text or "(未识别原文)"
        chinese = turn.translation_text
        if chinese or turn.source_text:
            self._on_subtitle(
                SubtitleEntry(english=english, chinese=chinese or "…", partial=False)
            )

        if self._tts_enabled() and turn.audio_pcm16:
            self._play_translation_audio(turn.audio_pcm16, turn.audio_sample_rate)

        if self._running:
            self._set_status("百炼英→中流式监听中…")

    def _play_translation_audio(self, pcm16: bytes, sample_rate: int) -> None:
        audio = pcm16_to_float32(pcm16)
        if len(audio) == 0:
            return

        def _run() -> None:
            with self._play_lock:
                self._tts_playing = True
                try:
                    self._set_status("播报中文…")
                    self._playout.play(audio, sample_rate)
                except Exception as exc:
                    logger.exception("播放百炼中文音频失败: %s", exc)
                    self._set_status(f"播放失败: {exc}")
                finally:
                    self._tts_playing = False
                    if self._running:
                        self._set_status("百炼英→中流式监听中…")

        threading.Thread(target=_run, daemon=True).start()

    def _set_status(self, msg: str) -> None:
        if self._on_status:
            self._on_status(msg)
