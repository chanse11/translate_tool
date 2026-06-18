"""英 → 中 流水线：环回采集 → VAD → ASR → 翻译 → 字幕（可选 TTS）。"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

import numpy as np

from audio.devices import get_default_output_device, resolve_loopback_device
from audio.loopback import LoopbackCapture
from audio.playout import AudioPlayout
from audio.vad import create_segmenter
from pipeline.resources import AppResources
from tts.edge_tts_player import EdgeTTSPlayer
from tts.tts_queue import TTSQueue

logger = logging.getLogger(__name__)


@dataclass
class SubtitleEntry:
    english: str
    chinese: str


class EnToZhPipeline:
    def __init__(
        self,
        config: dict,
        resources: AppResources,
        on_subtitle: callable,
        on_status: callable | None = None,
        is_paused: callable | None = None,
        tts_enabled: callable | None = None,
    ) -> None:
        self._config = config
        self._resources = resources
        audio_cfg = config.get("audio", {})
        vad_cfg = config.get("vad", {})
        trans_cfg = config.get("translation", {})
        tts_cfg = config.get("tts", {})
        si_cfg = config.get("si", {})

        self._sample_rate = audio_cfg.get("sample_rate", 16000)
        self._on_subtitle = on_subtitle
        self._on_status = on_status
        self._is_paused = is_paused or (lambda: False)
        self._tts_enabled = tts_enabled or (lambda: False)
        self._tts_chunked = si_cfg.get("tts_chunked", True)

        self._segmenter = create_segmenter(vad_cfg, self._sample_rate)
        self._from_code = trans_cfg.get("source_en", "en")
        self._to_code = trans_cfg.get("target_zh", "zh")

        self._monitor_index = get_default_output_device()
        self._tts = EdgeTTSPlayer(
            voice=tts_cfg.get("zh_voice", "zh-CN-XiaoxiaoNeural"),
            rate=tts_cfg.get("zh_rate", "+0%"),
        )
        self._playout = AudioPlayout(device_index=self._monitor_index)
        self._tts_queue = TTSQueue(
            self._tts,
            self._playout,
            monitor_index=None,
            test_mode=False,
        )

        self._capture: LoopbackCapture | None = None
        self._worker: threading.Thread | None = None
        self._queue: list[np.ndarray] = []
        self._queue_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._running = False
        self._tts_playing = False

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_tts_playing(self) -> bool:
        return self._tts_playing or self._tts_queue.is_playing or self._playout.is_playing

    def start(self) -> None:
        if not self._resources.is_ready:
            raise RuntimeError("模型尚未加载完成，请等待启动预加载结束。")
        if self._running:
            return
        loopback_keyword = self._config.get("audio", {}).get("loopback_device", "")
        device = resolve_loopback_device(loopback_keyword)
        if device is None:
            raise RuntimeError("未找到环回音频设备，请检查音频设置。")

        self._stop_event.clear()
        self._capture = LoopbackCapture(
            device_info=device,
            target_sample_rate=self._sample_rate,
            on_audio=self._on_audio_chunk,
        )
        self._worker = threading.Thread(target=self._process_loop, daemon=True)
        self._running = True
        self._capture.start()
        self._worker.start()
        self._set_status("英→中监听中…")
        logger.info("EnToZh 流水线已启动")

    def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        if self._capture:
            self._capture.stop()
            self._capture = None
        if self._worker:
            self._worker.join(timeout=5)
            self._worker = None
        self._set_status("英→中已停止")

    def _on_audio_chunk(self, chunk: np.ndarray) -> None:
        if not self._running:
            return
        for utterance in self._segmenter.feed(chunk):
            if self._is_paused():
                continue
            with self._queue_lock:
                self._queue.append(utterance)

    def _process_loop(self) -> None:
        while not self._stop_event.is_set():
            item: np.ndarray | None = None
            with self._queue_lock:
                if self._queue:
                    item = self._queue.pop(0)
            if item is None:
                self._stop_event.wait(0.05)
                continue
            if self._is_paused():
                with self._queue_lock:
                    self._queue.insert(0, item)
                self._stop_event.wait(0.1)
                continue
            try:
                self._set_status("识别英语中…")
                english = self._resources.en_asr.transcribe(
                    item, language="en", sample_rate=self._sample_rate
                )
                if not english.strip():
                    self._set_status("英→中监听中…")
                    continue
                self._set_status("翻译中…")
                chinese = self._resources.translator.translate(
                    english, self._from_code, self._to_code
                )
                entry = SubtitleEntry(english=english, chinese=chinese)
                self._on_subtitle(entry)

                if self._tts_enabled() and chinese.strip():
                    self._tts_playing = True
                    self._set_status("播报中文…")
                    self._tts_queue.play_text(chinese, chunked=self._tts_chunked)
                    self._tts_playing = False

                self._set_status("英→中监听中…")
            except Exception as exc:
                logger.exception("英→中处理失败: %s", exc)
                self._set_status(f"错误: {exc}")
                self._tts_playing = False

    def _set_status(self, msg: str) -> None:
        if self._on_status:
            self._on_status(msg)
