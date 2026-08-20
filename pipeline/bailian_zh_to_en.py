"""百炼中 → 英：麦克风 → LiveTranslate → 字幕 + 英文播到 VB-Cable。"""

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
from audio.playout import AudioPlayout, StreamingAudioPlayout
from bailian.livetranslate import BailianLiveTranslateSession, TurnResult
from pipeline.bailian_en_to_zh import pcm16_to_float32
from pipeline.zh_to_en import TranslationResult
from tts.edge_tts_player import EdgeTTSPlayer
from tts.tts_queue import TTSQueue

logger = logging.getLogger(__name__)

# 播放队列上限，超出时丢弃最旧条目，保证最新说话优先播出。
_MAX_PLAY_QUEUE = 10


@dataclass
class _PlayItem:
    pcm16: bytes
    sample_rate: int


class BailianZhToEnPipeline:
    def __init__(
        self,
        config: dict,
        api_key: str,
        on_result: callable,
        on_status: callable | None = None,
        on_tts_start: callable | None = None,
        on_tts_end: callable | None = None,
        is_test_mode: callable | None = None,
    ) -> None:
        self._config = config
        self._api_key = api_key
        audio_cfg = config.get("audio", {})
        bailian_cfg = config.get("bailian", {})
        tts_cfg = config.get("tts", {})
        si_cfg = config.get("si", {})
        test_cfg = config.get("test", {})

        self._sample_rate = audio_cfg.get("sample_rate", 16000)
        self._on_result = on_result
        self._on_status = on_status
        self._on_tts_start = on_tts_start
        self._on_tts_end = on_tts_end
        self._is_test_mode = is_test_mode or (lambda: False)
        self._input_mode = si_cfg.get("zh_input_mode", "continuous")

        self._model = bailian_cfg.get("model", "qwen3.5-livetranslate-flash-realtime")
        self._ws_base = bailian_cfg.get("ws_base", "")
        self._workspace_id = bailian_cfg.get("workspace_id", "")
        self._region = bailian_cfg.get("region", "cn-beijing")
        self._voice_mode = bailian_cfg.get("zh_en_voice_mode", "preset")
        self._voice = bailian_cfg.get("zh_en_voice", "Ethan")
        self._custom_voice_id = bailian_cfg.get("zh_en_custom_voice_id", "")
        self._silence_duration_ms = int(
            bailian_cfg.get("silence_duration_ms", 1000) or 1000
        )
        self._vad_threshold = float(bailian_cfg.get("vad_threshold", 0.2) or 0.2)
        self._prefix_padding_ms = int(
            bailian_cfg.get("prefix_padding_ms", 500) or 500
        )
        # 按住说话松手后垫静音（毫秒），帮助云端收尾；默认 250
        self._ptt_tail_silence_ms = int(
            bailian_cfg.get("ptt_tail_silence_ms", 250) or 250
        )
        self._ptt_tail_silence_ms = max(0, min(2000, self._ptt_tail_silence_ms))

        mic_index = resolve_input_device(audio_cfg.get("microphone_device", ""))
        self._virtual_cable_keyword = audio_cfg.get("virtual_cable_device", "CABLE Input")
        out_index = resolve_output_device(self._virtual_cable_keyword)
        self._monitor_index = get_default_output_device()

        self._mic = MicRecorder(mic_index, self._sample_rate)
        self._mic_stream: MicStreamCapture | None = None
        self._playout = AudioPlayout(out_index, sample_rate=24000)
        # 整句收齐再播；边收边播会把半句先开口，容易丢后半句、和下句抢
        self._stream_play = StreamingAudioPlayout(out_index, sample_rate=24000)
        self._stream_monitor = StreamingAudioPlayout(
            self._monitor_index, sample_rate=24000
        )
        self._stream_audio = bool(bailian_cfg.get("stream_audio", False))
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
        self._test_phrase = test_cfg.get(
            "phrase",
            "This is a test. Can you hear me clearly on the virtual microphone?",
        )

        self._session: BailianLiveTranslateSession | None = None
        self._continuous_running = False
        self._tts_playing = False
        self._processing = False
        self._play_lock = threading.Lock()
        self._sending_enabled = True
        self._play_queue: list[_PlayItem] = []
        self._play_queue_lock = threading.Lock()
        self._play_wake = threading.Event()
        self._play_stop = threading.Event()
        self._play_worker: threading.Thread | None = None
        self._ptt_chunks: list[np.ndarray] = []
        self._ptt_lock = threading.Lock()

    def update_credentials(self, api_key: str, config: dict | None = None) -> None:
        self._api_key = api_key
        if config is not None:
            self._config = config
            bailian_cfg = config.get("bailian", {})
            self._model = bailian_cfg.get("model", self._model)
            self._ws_base = bailian_cfg.get("ws_base", self._ws_base)
            self._workspace_id = bailian_cfg.get("workspace_id", self._workspace_id)
            self._region = bailian_cfg.get("region", self._region)
            self._voice_mode = bailian_cfg.get("zh_en_voice_mode", self._voice_mode)
            self._voice = bailian_cfg.get("zh_en_voice", self._voice)
            self._custom_voice_id = bailian_cfg.get(
                "zh_en_custom_voice_id", self._custom_voice_id
            )
            self._silence_duration_ms = int(
                bailian_cfg.get("silence_duration_ms", self._silence_duration_ms)
                or self._silence_duration_ms
            )
            self._vad_threshold = float(
                bailian_cfg.get("vad_threshold", self._vad_threshold)
                or self._vad_threshold
            )
            self._prefix_padding_ms = int(
                bailian_cfg.get("prefix_padding_ms", self._prefix_padding_ms)
                or self._prefix_padding_ms
            )
            self._ptt_tail_silence_ms = int(
                bailian_cfg.get("ptt_tail_silence_ms", self._ptt_tail_silence_ms)
                or self._ptt_tail_silence_ms
            )
            self._ptt_tail_silence_ms = max(0, min(2000, self._ptt_tail_silence_ms))
            self._stream_audio = bool(
                bailian_cfg.get("stream_audio", self._stream_audio)
            )
            self._input_mode = self._config.get("si", {}).get(
                "zh_input_mode", self._input_mode
            )

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
        with self._play_queue_lock:
            queued = len(self._play_queue) > 0
        return (
            self._tts_playing
            or queued
            or self._tts_queue.is_playing
            or self._playout.is_playing
            or self._stream_play.is_playing
            or (self._is_test_mode() and self._stream_monitor.is_playing)
        )

    @property
    def play_queue_size(self) -> int:
        with self._play_queue_lock:
            return len(self._play_queue) + (1 if self._tts_playing else 0)

    def _ensure_api_key(self) -> None:
        if not self._api_key.strip():
            raise RuntimeError("请先填写阿里百炼 API Key")

    def _ensure_virtual_cable(self) -> None:
        if self._playout.device_index is None:
            raise RuntimeError(
                "未找到 VB-Cable 输出设备。请安装 VB-Audio Virtual Cable，"
                f"或在 config.yaml 中设置 virtual_cable_device（当前: {self._virtual_cable_keyword}）。"
            )

    def _create_session(self) -> BailianLiveTranslateSession:
        voice = self._voice.strip() or "Ethan"
        enable_voice_clone = False
        voice_clone_frequency = None
        custom_voice_id = self._custom_voice_id.strip()

        if self._voice_mode == "clone_once":
            voice = "default"
            enable_voice_clone = True
            voice_clone_frequency = "once"
        elif self._voice_mode == "clone_always":
            voice = "default"
            enable_voice_clone = True
            voice_clone_frequency = "always"
        elif self._voice_mode == "custom":
            if not custom_voice_id:
                raise RuntimeError("请填写已复刻的百炼音色 ID")
            voice = custom_voice_id
            enable_voice_clone = True
            voice_clone_frequency = "never"

        return BailianLiveTranslateSession(
            api_key=self._api_key,
            source_language="zh",
            target_language="en",
            model=self._model,
            ws_base=self._ws_base,
            workspace_id=self._workspace_id,
            region=self._region,
            audio_output=True,
            enable_source_asr=True,
            voice=voice,
            enable_voice_clone=enable_voice_clone,
            voice_clone_frequency=voice_clone_frequency,
            silence_duration_ms=self._silence_duration_ms,
            vad_threshold=self._vad_threshold,
            prefix_padding_ms=self._prefix_padding_ms,
            manual_turn_detection=(self._input_mode == "ptt"),
            on_partial=self._on_partial,
            on_turn=self._on_turn,
            on_audio_delta=self._on_audio_delta if self._stream_audio else None,
            on_status=self._set_status,
            on_error=self._set_status,
        )

    def _on_partial(self, source: str, translation: str) -> None:
        self._on_result(
            TranslationResult(
                chinese=source or "…",
                english=translation or "…",
                partial=True,
            )
        )

    def start_continuous(self) -> None:
        self._ensure_api_key()
        self._ensure_virtual_cable()
        if self._continuous_running:
            return

        mic_index = resolve_input_device(
            self._config.get("audio", {}).get("microphone_device", "")
        )
        self._session = self._create_session()
        self._sending_enabled = True
        self._clear_play_queue()
        self._ensure_play_worker()
        self._mic_stream = MicStreamCapture(
            device_index=mic_index,
            sample_rate=self._sample_rate,
            on_audio=self._on_audio_chunk,
        )
        self._continuous_running = True
        # 先开麦缓存，再连百炼，避免点「开始」后立刻说话时句首丢失
        self._mic_stream.start()
        try:
            self._session.start()
        except Exception:
            self._continuous_running = False
            self._sending_enabled = False
            self._mic_stream.stop()
            self._mic_stream = None
            self._session = None
            raise
        if self._voice_mode in ("clone_once", "clone_always"):
            self._set_status(
                "百炼中→英流式监听中…（音色复刻中，前几句可能仍为默认音色）"
            )
        else:
            self._set_status("百炼中→英流式监听中…")
        logger.info(
            "Bailian ZhToEn 流式模式已启动 voice_mode=%s stream_audio=%s",
            self._voice_mode,
            self._stream_audio,
        )

    def stop_continuous(self) -> None:
        self._continuous_running = False
        self._sending_enabled = False
        if self._mic_stream:
            self._mic_stream.stop()
            self._mic_stream = None
        if self._session:
            self._session.stop()
            self._session = None
        self._stop_play_worker()
        self._close_stream_play()
        self._set_status("中→英连续监听已停止")

    def _on_audio_chunk(self, chunk: np.ndarray) -> None:
        if not self._continuous_running or not self._session:
            return
        # 播放中仍持续上送麦克风，实现边听边译并排队播报。
        if not self._sending_enabled:
            return
        self._session.send_float32(chunk)

    def ptt_press(self) -> None:
        if self._input_mode != "ptt":
            return
        self._ensure_api_key()
        self._ensure_virtual_cable()
        self._ensure_play_worker()
        if self._session is None or not self._session.is_connected:
            self._session = self._create_session()
            self._session.start()
        # 清空上一轮未提交缓冲，避免脏音频影响复刻
        if self._session and self._session.is_connected:
            self._session.clear_audio_buffer()
        with self._ptt_lock:
            self._ptt_chunks = []
        self._sending_enabled = True
        self._processing = True
        mic_index = resolve_input_device(
            self._config.get("audio", {}).get("microphone_device", "")
        )
        # 按住说话期间保持同一条采集流，避免每次重开设备
        if self._mic_stream is None or not self._mic_stream.is_running:
            if self._mic_stream is not None:
                self._mic_stream.stop()
            self._mic_stream = MicStreamCapture(
                device_index=mic_index,
                sample_rate=self._sample_rate,
                on_audio=self._on_ptt_audio_chunk,
            )
            self._mic_stream.start()
        pending = self.play_queue_size
        if pending > 0:
            self._set_status(f"录音中…（松手排队，待播 {pending} 条）")
        else:
            self._set_status("录音中…（松手等待翻译）")

    def _on_ptt_audio_chunk(self, chunk: np.ndarray) -> None:
        if self._input_mode != "ptt" or not self._sending_enabled:
            return
        if self._session is None:
            return
        with self._ptt_lock:
            self._ptt_chunks.append(np.asarray(chunk, dtype=np.float32).copy())
        self._session.send_float32(chunk)

    def ptt_release(self) -> None:
        if self._input_mode != "ptt":
            return
        self._sending_enabled = False
        # 先停采集再统计本轮音频，避免尾包竞态
        if self._mic_stream and not self._continuous_running:
            self._mic_stream.stop()
            self._mic_stream = None
        with self._ptt_lock:
            chunks = self._ptt_chunks
            self._ptt_chunks = []
        if chunks:
            audio = np.concatenate(chunks)
            dur = float(len(audio)) / float(self._sample_rate or 16000)
            rms = float(np.sqrt(np.mean(np.square(audio)))) if len(audio) else 0.0
            logger.info("PTT 本轮音频: duration=%.2fs rms=%.4f chunks=%s", dur, rms, len(chunks))
            if self._voice_mode in ("clone_once", "clone_always") and dur < 2.5:
                self._set_status(
                    f"本轮仅 {dur:.1f}s，实时复刻偏弱；请按住说满 3–5 秒，或先「录制我的音色」"
                )
            if rms < 0.005:
                self._set_status("麦克风音量过低，复刻会失败；请检查插孔麦/增益")
                logger.warning("PTT 音量过低 rms=%.5f", rms)
        if self._session and self._session.is_connected:
            # Manual：等本地队列发完再 commit，保证整段进缓冲区
            self._session.commit_audio(wait_flush=True, timeout=2.0)
        self._set_status("等待百炼结果…")

    def _on_audio_delta(self, pcm16: bytes, sample_rate: int) -> None:
        """百炼英文音频分片到达后立即写入虚拟麦。"""
        if not pcm16 or not self._stream_audio:
            return
        first = False
        with self._play_lock:
            if not self._tts_playing:
                self._tts_playing = True
                first = True
        if first and self._on_tts_start:
            try:
                self._on_tts_start()
            except Exception:
                logger.exception("on_tts_start 回调失败")
            self._set_status("播报英文…（流式）")
        try:
            self._stream_play.write_pcm16(pcm16, sample_rate)
            if self._is_test_mode():
                self._stream_monitor.write_pcm16(pcm16, sample_rate)
        except Exception as exc:
            logger.exception("流式播放英文分片失败: %s", exc)
            self._set_status(f"流式播放失败: {exc}")

    def _close_stream_play(self) -> None:
        try:
            self._stream_play.close()
        except Exception:
            logger.debug("关闭流式播放失败", exc_info=True)
        try:
            self._stream_monitor.close()
        except Exception:
            logger.debug("关闭监听流式播放失败", exc_info=True)
        with self._play_lock:
            was_playing = self._tts_playing
            self._tts_playing = False
        if was_playing and self._on_tts_end:
            try:
                self._on_tts_end()
            except Exception:
                logger.exception("on_tts_end 回调失败")

    def _on_turn(self, turn: TurnResult) -> None:
        chinese = turn.source_text or "(未识别原文)"
        english = turn.translation_text
        warning = ""
        if not turn.audio_pcm16 and english:
            warning = "百炼未返回英文语音，请重说这一句。"
            self._set_status(warning)

        if english or turn.source_text:
            self._on_result(
                TranslationResult(
                    chinese=chinese,
                    english=english or "…",
                    partial=False,
                    warning=warning,
                )
            )

        if self._stream_audio:
            # 音频已在 delta 回调中播出，整轮结束只收尾状态，避免重复播放
            if turn.audio_pcm16:
                with self._play_lock:
                    was_playing = self._tts_playing
                    self._tts_playing = False
                if was_playing and self._on_tts_end:
                    try:
                        self._on_tts_end()
                    except Exception:
                        logger.exception("on_tts_end 回调失败")
        elif turn.audio_pcm16:
            self._enqueue_play(turn.audio_pcm16, turn.audio_sample_rate)

        self._processing = False
        pending = self.play_queue_size
        if self._continuous_running:
            if not turn.audio_pcm16:
                self._set_status("百炼中→英流式监听中（上一句需重说）")
            elif pending > 1:
                self._set_status(f"百炼中→英流式监听中…（待播 {pending} 条）")
            else:
                self._set_status("百炼中→英流式监听中…")
        elif self._input_mode == "ptt":
            if not turn.audio_pcm16:
                self._set_status("未返回英文语音，请重说")
            elif pending > 1:
                self._set_status(f"已入队，待播 {pending} 条")
            else:
                self._set_status("就绪")
    def _clear_play_queue(self) -> None:
        with self._play_queue_lock:
            self._play_queue.clear()
        self._play_wake.set()

    def _ensure_play_worker(self) -> None:
        if self._play_worker is not None and self._play_worker.is_alive():
            return
        self._play_stop.clear()
        self._play_worker = threading.Thread(target=self._play_loop, daemon=True)
        self._play_worker.start()

    def _stop_play_worker(self) -> None:
        self._play_stop.set()
        self._clear_play_queue()
        if self._play_worker is not None:
            self._play_worker.join(timeout=3)
            self._play_worker = None

    def _enqueue_play(self, pcm16: bytes, sample_rate: int) -> None:
        if not pcm16:
            return
        self._ensure_play_worker()
        with self._play_queue_lock:
            if len(self._play_queue) >= _MAX_PLAY_QUEUE:
                dropped = self._play_queue.pop(0)
                logger.warning(
                    "播放队列已满，丢弃最旧条目 (%d bytes)",
                    len(dropped.pcm16),
                )
            self._play_queue.append(_PlayItem(pcm16=pcm16, sample_rate=sample_rate))
            pending = len(self._play_queue) + (1 if self._tts_playing else 0)
        self._play_wake.set()
        if pending > 1:
            self._set_status(f"译文已入队，待播 {pending} 条")

    def _play_loop(self) -> None:
        while not self._play_stop.is_set():
            self._play_wake.wait(timeout=0.2)
            self._play_wake.clear()
            while not self._play_stop.is_set():
                item: _PlayItem | None = None
                with self._play_queue_lock:
                    if self._play_queue:
                        item = self._play_queue.pop(0)
                if item is None:
                    break
                self._play_one(item)

    def _play_one(self, item: _PlayItem) -> None:
        audio = pcm16_to_float32(item.pcm16)
        if len(audio) == 0:
            return
        with self._play_lock:
            self._tts_playing = True
            try:
                if self._on_tts_start:
                    self._on_tts_start()
                remaining = self.play_queue_size
                if remaining > 1:
                    self._set_status(f"播报英文…（队列剩 {remaining - 1} 条）")
                else:
                    self._set_status("播报英文…")
                if self._is_test_mode():
                    self._playout.play_dual(audio, item.sample_rate, self._monitor_index)
                else:
                    self._playout.play(audio, item.sample_rate)
            except Exception as exc:
                logger.exception("播放百炼英文音频失败: %s", exc)
                self._set_status(f"播放失败: {exc}")
            finally:
                self._tts_playing = False
                if self._on_tts_end:
                    self._on_tts_end()
                if self._continuous_running:
                    pending = self.play_queue_size
                    if pending > 0:
                        self._set_status(f"百炼中→英流式监听中…（待播 {pending} 条）")
                    else:
                        self._set_status("百炼中→英流式监听中…")
                else:
                    pending = self.play_queue_size
                    if pending > 0:
                        self._set_status(f"待播 {pending} 条…")
                    else:
                        self._set_status("就绪")

    def _play_english_audio(self, pcm16: bytes, sample_rate: int) -> None:
        self._enqueue_play(pcm16, sample_rate)

    def _fallback_edge_tts(self, english: str) -> None:
        def _run() -> None:
            with self._play_lock:
                self._tts_playing = True
                try:
                    if self._on_tts_start:
                        self._on_tts_start()
                    self._tts_queue.set_test_mode(self._is_test_mode())
                    self._tts_queue.play_text(english, chunked=True)
                except Exception as exc:
                    logger.exception("Edge TTS 回退失败: %s", exc)
                    self._set_status(f"TTS 失败: {exc}")
                finally:
                    self._tts_playing = False
                    if self._on_tts_end:
                        self._on_tts_end()

        self._ensure_play_worker()
        threading.Thread(target=_run, daemon=True).start()

    def test_virtual_mic(self) -> None:
        if self.is_tts_playing or self._processing:
            self._set_status("当前忙碌，请稍候再试")
            return
        self._ensure_virtual_cable()
        threading.Thread(target=self._run_virtual_mic_test, daemon=True).start()

    def _run_virtual_mic_test(self) -> None:
        self._processing = True
        try:
            self._set_status("测试：合成英文语音…")
            self._tts_queue.set_test_mode(self._is_test_mode())
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

    def _set_status(self, msg: str) -> None:
        if self._on_status:
            self._on_status(msg)
