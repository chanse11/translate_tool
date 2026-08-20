"""中 → 英 流水线：VAD 连续 / PTT → ASR → 翻译 → 分句 TTS → VB-Cable。"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

import numpy as np

from audio.devices import (
    get_default_output_device,
    resolve_input_device,
    resolve_output_device,
)
from audio.mic import MicRecorder
from audio.mic_stream import MicStreamCapture
from audio.playout import AudioPlayout
from audio.vad import create_segmenter
from pipeline.resources import AppResources
from tts.edge_tts_player import EdgeTTSPlayer
from tts.tts_queue import TTSQueue

logger = logging.getLogger(__name__)

_MAX_UTTERANCE_QUEUE = 10
_MAX_TTS_QUEUE = 10


@dataclass
class TranslationResult:
    chinese: str
    english: str
    partial: bool = False
    warning: str = ""


class ZhToEnPipeline:
    def __init__(
        self,
        config: dict,
        resources: AppResources,
        on_result: callable,
        on_status: callable | None = None,
        on_tts_start: callable | None = None,
        on_tts_end: callable | None = None,
        is_test_mode: callable | None = None,
    ) -> None:
        self._config = config
        self._resources = resources
        audio_cfg = config.get("audio", {})
        trans_cfg = config.get("translation", {})
        tts_cfg = config.get("tts", {})
        vad_cfg = config.get("vad", {})
        si_cfg = config.get("si", {})

        self._sample_rate = audio_cfg.get("sample_rate", 16000)
        self._on_result = on_result
        self._on_status = on_status
        self._on_tts_start = on_tts_start
        self._on_tts_end = on_tts_end
        self._is_test_mode = is_test_mode or (lambda: False)
        self._monitor_index = get_default_output_device()
        self._input_mode = si_cfg.get("zh_input_mode", "continuous")
        self._tts_chunked = si_cfg.get("tts_chunked", True)

        mic_index = resolve_input_device(audio_cfg.get("microphone_device", ""))
        self._virtual_cable_keyword = audio_cfg.get("virtual_cable_device", "CABLE Input")
        out_index = resolve_output_device(self._virtual_cable_keyword)

        self._mic = MicRecorder(mic_index, self._sample_rate)
        self._mic_stream: MicStreamCapture | None = None
        self._playout = AudioPlayout(out_index)
        self._tts = EdgeTTSPlayer(
            voice=tts_cfg.get("voice", "en-US-GuyNeural"),
            rate=tts_cfg.get("rate", "+0%"),
        )
        self._tts_queue = TTSQueue(
            self._tts,
            self._playout,
            monitor_index=self._monitor_index,
            test_mode=False,
        )
        self._segmenter = create_segmenter(vad_cfg, self._sample_rate)
        self._from_code = trans_cfg.get("source_zh", "zh")
        self._to_code = trans_cfg.get("target_en", "en")
        test_cfg = config.get("test", {})
        self._test_phrase = test_cfg.get(
            "phrase",
            "This is a test. Can you hear me clearly on the virtual microphone?",
        )

        self._processing = False
        self._tts_playing = False
        self._continuous_running = False
        self._worker: threading.Thread | None = None
        self._queue: list[np.ndarray] = []
        self._queue_lock = threading.Lock()
        self._stop_event = threading.Event()

        self._tts_play_queue: list[str] = []
        self._tts_play_lock = threading.Lock()
        self._tts_play_wake = threading.Event()
        self._tts_play_stop = threading.Event()
        self._tts_play_worker: threading.Thread | None = None

    @property
    def input_mode(self) -> str:
        return self._input_mode

    @property
    def is_recording(self) -> bool:
        return self._mic.is_recording

    @property
    def is_continuous_running(self) -> bool:
        return self._continuous_running

    @property
    def is_processing(self) -> bool:
        return self._processing

    @property
    def is_tts_playing(self) -> bool:
        with self._tts_play_lock:
            queued = len(self._tts_play_queue) > 0
        return (
            self._tts_playing
            or queued
            or self._tts_queue.is_playing
            or self._playout.is_playing
        )

    @property
    def play_queue_size(self) -> int:
        with self._tts_play_lock:
            return len(self._tts_play_queue) + (1 if self._tts_playing else 0)

    def _require_ready(self) -> None:
        if not self._resources.is_ready:
            raise RuntimeError("模型尚未加载完成，请等待启动预加载结束。")

    def _ensure_virtual_cable(self) -> None:
        if self._playout.device_index is None:
            raise RuntimeError(
                "未找到 VB-Cable 输出设备。请安装 VB-Audio Virtual Cable，"
                f"或在 config.yaml 中设置 virtual_cable_device（当前: {self._virtual_cable_keyword}）。"
            )

    def _sync_tts_queue_test_mode(self) -> None:
        self._tts_queue.set_test_mode(self._is_test_mode())

    def start_continuous(self) -> None:
        """VAD 连续模式：自动检测中文语句并翻译播报。"""
        self._require_ready()
        if self._continuous_running:
            return
        self._ensure_virtual_cable()
        mic_index = resolve_input_device(
            self._config.get("audio", {}).get("microphone_device", "")
        )
        self._stop_event.clear()
        self._clear_tts_play_queue()
        self._ensure_tts_play_worker()
        self._mic_stream = MicStreamCapture(
            device_index=mic_index,
            sample_rate=self._sample_rate,
            on_audio=self._on_audio_chunk,
        )
        self._worker = threading.Thread(target=self._process_loop, daemon=True)
        self._continuous_running = True
        self._mic_stream.start()
        self._worker.start()
        self._set_status("中→英连续监听中…")
        logger.info("ZhToEn 连续模式已启动")

    def stop_continuous(self) -> None:
        self._continuous_running = False
        self._stop_event.set()
        if self._mic_stream:
            self._mic_stream.stop()
            self._mic_stream = None
        pending = self._segmenter.flush_pending()
        if pending is not None and len(pending) > 0:
            with self._queue_lock:
                self._queue.append(pending)
        if self._worker:
            self._worker.join(timeout=5)
            self._worker = None
        self._stop_tts_play_worker()
        self._segmenter.reset()
        self._set_status("中→英连续监听已停止")

    def _on_audio_chunk(self, chunk: np.ndarray) -> None:
        if not self._continuous_running:
            return
        for utterance in self._segmenter.feed(chunk):
            # 播放/处理中仍入队，实现边说边译并排队播报。
            with self._queue_lock:
                if len(self._queue) >= _MAX_UTTERANCE_QUEUE:
                    self._queue.pop(0)
                    logger.warning("语音队列已满，丢弃最旧片段")
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
            # 识别+翻译与播放解耦：播报中仍继续处理后续语音。
            self._process(item)

    def test_virtual_mic(self) -> None:
        """向虚拟麦克风发送测试语音（遵循测试模式设置）。"""
        self._require_ready()
        if self.is_tts_playing or self._processing:
            self._set_status("当前忙碌，请稍候再试")
            return
        threading.Thread(target=self._run_virtual_mic_test, daemon=True).start()

    def _run_virtual_mic_test(self) -> None:
        self._processing = True
        try:
            self._ensure_virtual_cable()
            self._set_status("测试：合成英文语音…")
            self._sync_tts_queue_test_mode()
            if self._on_tts_start:
                self._on_tts_start()
            self._tts_playing = True
            self._tts_queue.play_text(self._test_phrase, chunked=False)
            self._set_status("测试完成")
        except Exception as exc:
            logger.exception("虚拟麦克风测试失败: %s", exc)
            self._set_status(f"测试失败: {exc}")
        finally:
            self._tts_playing = False
            self._processing = False
            if self._on_tts_end:
                self._on_tts_end()

    def ptt_press(self) -> None:
        if self._input_mode != "ptt":
            return
        self._require_ready()
        self._ensure_virtual_cable()
        self._ensure_tts_play_worker()
        self._mic.start()
        pending = self.play_queue_size
        if pending > 0:
            self._set_status(f"录音中…（松手排队，待播 {pending} 条）")
        else:
            self._set_status("录音中…（松手发送）")

    def ptt_release(self) -> None:
        if self._input_mode != "ptt":
            return
        if not self._mic.is_recording:
            return
        audio = self._mic.stop()
        if len(audio) < self._sample_rate * 0.3:
            self._set_status("录音太短，请重试")
            return
        threading.Thread(target=self._process, args=(audio,), daemon=True).start()

    def _process(self, audio: np.ndarray) -> None:
        self._processing = True
        try:
            self._set_status("识别中文…")
            chinese = self._resources.zh_asr.transcribe(
                audio, language="zh", sample_rate=self._sample_rate
            )
            if not chinese.strip():
                self._set_status(
                    "未识别到中文，请重试"
                    if self._input_mode == "ptt"
                    else "中→英连续监听中…"
                )
                return

            self._set_status("翻译为英文…")
            english = self._resources.translator.translate(
                chinese, self._from_code, self._to_code
            )
            if not english.strip():
                self._set_status(
                    "翻译失败，请重试"
                    if self._input_mode == "ptt"
                    else "中→英连续监听中…"
                )
                return

            result = TranslationResult(chinese=chinese, english=english)
            self._on_result(result)
            self._enqueue_tts(english)
            pending = self.play_queue_size
            if self._continuous_running:
                if pending > 1:
                    self._set_status(f"中→英连续监听中…（待播 {pending} 条）")
                else:
                    self._set_status("中→英连续监听中…")
            elif pending > 1:
                self._set_status(f"已入队，待播 {pending} 条")
            else:
                self._set_status("就绪")
        except Exception as exc:
            logger.exception("中→英处理失败: %s", exc)
            self._set_status(f"错误: {exc}")
        finally:
            self._processing = False

    def _clear_tts_play_queue(self) -> None:
        with self._tts_play_lock:
            self._tts_play_queue.clear()
        self._tts_play_wake.set()

    def _ensure_tts_play_worker(self) -> None:
        if self._tts_play_worker is not None and self._tts_play_worker.is_alive():
            return
        self._tts_play_stop.clear()
        self._tts_play_worker = threading.Thread(
            target=self._tts_play_loop, daemon=True
        )
        self._tts_play_worker.start()

    def _stop_tts_play_worker(self) -> None:
        self._tts_play_stop.set()
        self._clear_tts_play_queue()
        if self._tts_play_worker is not None:
            self._tts_play_worker.join(timeout=5)
            self._tts_play_worker = None

    def _enqueue_tts(self, english: str) -> None:
        text = english.strip()
        if not text:
            return
        self._ensure_tts_play_worker()
        with self._tts_play_lock:
            if len(self._tts_play_queue) >= _MAX_TTS_QUEUE:
                dropped = self._tts_play_queue.pop(0)
                logger.warning("TTS 队列已满，丢弃最旧条目: %s", dropped[:40])
            self._tts_play_queue.append(text)
            pending = len(self._tts_play_queue) + (1 if self._tts_playing else 0)
        self._tts_play_wake.set()
        if pending > 1:
            self._set_status(f"译文已入队，待播 {pending} 条")

    def _tts_play_loop(self) -> None:
        while not self._tts_play_stop.is_set():
            self._tts_play_wake.wait(timeout=0.2)
            self._tts_play_wake.clear()
            while not self._tts_play_stop.is_set():
                text: str | None = None
                with self._tts_play_lock:
                    if self._tts_play_queue:
                        text = self._tts_play_queue.pop(0)
                if text is None:
                    break
                self._play_tts_text(text)

    def _play_tts_text(self, english: str) -> None:
        try:
            self._sync_tts_queue_test_mode()
            if self._on_tts_start:
                self._on_tts_start()
            self._tts_playing = True
            remaining = self.play_queue_size
            if remaining > 1:
                self._set_status(f"合成英文语音…（队列剩 {remaining - 1} 条）")
            else:
                self._set_status("合成英文语音…")
            self._tts_queue.play_text(english, chunked=self._tts_chunked)
        except Exception as exc:
            logger.exception("TTS 播放失败: %s", exc)
            self._set_status(f"TTS 失败: {exc}")
        finally:
            self._tts_playing = False
            if self._on_tts_end:
                self._on_tts_end()
            if self._continuous_running:
                pending = self.play_queue_size
                if pending > 0:
                    self._set_status(f"中→英连续监听中…（待播 {pending} 条）")
                else:
                    self._set_status("中→英连续监听中…")
            else:
                pending = self.play_queue_size
                if pending > 0:
                    self._set_status(f"待播 {pending} 条…")
                else:
                    self._set_status("就绪")

    def _set_status(self, msg: str) -> None:
        if self._on_status:
            self._on_status(msg)
