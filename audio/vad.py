"""WebRTC VAD 语音活动检测，用于环回/麦克风音频断句。"""

from __future__ import annotations

import logging

import numpy as np
import webrtcvad

logger = logging.getLogger(__name__)

FRAME_MS = 30  # WebRTC VAD 支持 10/20/30 ms


class SpeechSegmenter:
    """从连续音频流中检测完整语句边界。

    改进点：
    - pre-roll：语音开始前保留一小段音频，避免首字被切掉
    - tail trim：句末只保留少量静音填充，去掉过长尾部静音
    - 连续静音帧计数：减少 VAD 抖动导致的误切
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        aggressiveness: int = 1,
        silence_ms: int = 700,
        min_speech_ms: int = 400,
        max_speech_ms: int = 12000,
        pre_roll_ms: int = 200,
        tail_pad_ms: int = 150,
    ) -> None:
        if sample_rate not in (8000, 16000, 32000, 48000):
            raise ValueError(f"不支持的采样率: {sample_rate}")
        self._sample_rate = sample_rate
        self._frame_samples = int(sample_rate * FRAME_MS / 1000)
        self._vad = webrtcvad.Vad(aggressiveness)
        self._silence_frames = max(1, round(silence_ms / FRAME_MS))
        self._min_speech_frames = max(1, round(min_speech_ms / FRAME_MS))
        self._max_speech_frames = max(1, round(max_speech_ms / FRAME_MS))
        self._pre_roll_frames = max(0, round(pre_roll_ms / FRAME_MS))
        self._tail_pad_frames = max(0, round(tail_pad_ms / FRAME_MS))

        self._buffer = np.array([], dtype=np.float32)
        self._pre_roll: list[np.ndarray] = []
        self._speech_frames: list[np.ndarray] = []
        self._silence_count = 0
        self._in_speech = False
        self._speech_frame_count = 0

    def feed(self, chunk: np.ndarray) -> list[np.ndarray]:
        """喂入音频块，返回检测到的完整语句列表。"""
        if len(chunk) == 0:
            return []
        self._buffer = np.concatenate([self._buffer, chunk.astype(np.float32)])
        completed: list[np.ndarray] = []

        while len(self._buffer) >= self._frame_samples:
            frame = self._buffer[: self._frame_samples]
            self._buffer = self._buffer[self._frame_samples :]
            is_speech = self._is_speech_frame(frame)

            if is_speech:
                if not self._in_speech:
                    self._begin_speech()
                self._speech_frames.append(frame)
                self._silence_count = 0
                self._speech_frame_count += 1
                if self._speech_frame_count >= self._max_speech_frames:
                    completed.append(self._flush())
            elif self._in_speech:
                self._speech_frames.append(frame)
                self._silence_count += 1
                if self._silence_count >= self._silence_frames:
                    if self._speech_frame_count >= self._min_speech_frames:
                        completed.append(self._flush())
                    else:
                        self._reset()

            self._push_pre_roll(frame, is_speech)

        return completed

    def flush_pending(self) -> np.ndarray | None:
        """结束采集时冲刷未完成的语音段（可选）。"""
        if not self._in_speech or self._speech_frame_count < self._min_speech_frames:
            self._reset()
            return None
        return self._flush()

    def reset(self) -> None:
        """重置内部状态。"""
        self._buffer = np.array([], dtype=np.float32)
        self._pre_roll.clear()
        self._reset()

    def _is_speech_frame(self, frame: np.ndarray) -> bool:
        pcm = _float_to_pcm16(frame)
        return self._vad.is_speech(pcm, self._sample_rate)

    def _begin_speech(self) -> None:
        self._in_speech = True
        self._speech_frame_count = 0
        if self._pre_roll_frames > 0 and self._pre_roll:
            self._speech_frames.extend(self._pre_roll[-self._pre_roll_frames :])

    def _push_pre_roll(self, frame: np.ndarray, is_speech: bool) -> None:
        if self._pre_roll_frames <= 0:
            return
        self._pre_roll.append(frame)
        max_len = self._pre_roll_frames + 2
        if len(self._pre_roll) > max_len:
            self._pre_roll = self._pre_roll[-max_len:]

    def _flush(self) -> np.ndarray:
        frames = list(self._speech_frames)
        audio = np.concatenate(frames) if frames else np.array([], dtype=np.float32)
        audio = _trim_trailing_silence(
            frames,
            self._tail_pad_frames,
            self._frame_samples,
            self._is_speech_frame,
        )
        self._reset()
        if len(audio) > 0:
            logger.debug("VAD 断句: %.2fs", len(audio) / self._sample_rate)
        return audio

    def _reset(self) -> None:
        self._speech_frames.clear()
        self._silence_count = 0
        self._in_speech = False
        self._speech_frame_count = 0


def _trim_trailing_silence(
    frames: list[np.ndarray],
    tail_pad_frames: int,
    frame_samples: int,
    is_speech_fn: callable,
) -> np.ndarray:
    """去掉句末过长静音，只保留少量尾部填充。"""
    if not frames:
        return np.array([], dtype=np.float32)

    speech_end = len(frames)
    for idx in range(len(frames) - 1, -1, -1):
        if is_speech_fn(frames[idx]):
            speech_end = idx + 1
            break

    keep_until = min(len(frames), speech_end + tail_pad_frames)
    trimmed = frames[:keep_until]
    if not trimmed:
        return np.array([], dtype=np.float32)
    return np.concatenate(trimmed)


def create_segmenter(vad_cfg: dict, sample_rate: int = 16000) -> SpeechSegmenter:
    """从 config['vad'] 创建 SpeechSegmenter。"""
    return SpeechSegmenter(
        sample_rate=sample_rate,
        aggressiveness=vad_cfg.get("aggressiveness", 1),
        silence_ms=vad_cfg.get("silence_ms", 700),
        min_speech_ms=vad_cfg.get("min_speech_ms", 400),
        max_speech_ms=vad_cfg.get("max_speech_ms", 12000),
        pre_roll_ms=vad_cfg.get("pre_roll_ms", 200),
        tail_pad_ms=vad_cfg.get("tail_pad_ms", 150),
    )


def _float_to_pcm16(samples: np.ndarray) -> bytes:
    clipped = np.clip(samples, -1.0, 1.0)
    pcm = (clipped * 32767).astype(np.int16)
    return pcm.tobytes()
