"""VAD 断句单元测试。"""

from __future__ import annotations

import numpy as np

from audio.vad import SpeechSegmenter, create_segmenter


def _silence(seconds: float, sr: int = 16000) -> np.ndarray:
    return np.zeros(int(seconds * sr), dtype=np.float32)


def _tone(seconds: float, sr: int = 16000, freq: float = 440.0) -> np.ndarray:
    t = np.linspace(0, seconds, int(seconds * sr), endpoint=False)
    return (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _feed_all(segmenter: SpeechSegmenter, audio: np.ndarray, chunk_ms: int = 30) -> list[np.ndarray]:
    chunk = int(16000 * chunk_ms / 1000)
    out: list[np.ndarray] = []
    for i in range(0, len(audio), chunk):
        out.extend(segmenter.feed(audio[i : i + chunk]))
    return out


def test_single_utterance_with_internal_pause():
    """句中短停顿不应切句。"""
    seg = create_segmenter(
        {
            "aggressiveness": 1,
            "silence_ms": 700,
            "min_speech_ms": 400,
            "max_speech_ms": 12000,
            "pre_roll_ms": 200,
            "tail_pad_ms": 150,
        }
    )
    audio = np.concatenate(
        [
            _silence(0.3),
            _tone(1.0),
            _silence(0.35),  # 350ms 停顿
            _tone(1.0),
            _silence(0.9),
        ]
    )
    segments = _feed_all(seg, audio)
    assert len(segments) == 1, f"expected 1 segment, got {len(segments)}"


def test_two_utterances():
    """两句之间长静音应切为两段。"""
    seg = create_segmenter(
        {
            "aggressiveness": 1,
            "silence_ms": 700,
            "min_speech_ms": 400,
            "max_speech_ms": 12000,
        }
    )
    audio = np.concatenate(
        [
            _tone(1.0),
            _silence(0.9),
            _tone(1.0),
            _silence(0.9),
        ]
    )
    segments = _feed_all(seg, audio)
    assert len(segments) == 2, f"expected 2 segments, got {len(segments)}"


def test_pre_roll_not_empty_on_short_speech():
    seg = SpeechSegmenter(
        sample_rate=16000,
        aggressiveness=1,
        silence_ms=500,
        min_speech_ms=300,
        pre_roll_ms=200,
    )
    audio = np.concatenate([_silence(0.5), _tone(0.8), _silence(0.7)])
    segments = _feed_all(seg, audio)
    assert len(segments) == 1
    assert len(segments[0]) > 0.8 * 16000 * 0.5  # 应含 pre-roll


if __name__ == "__main__":
    test_single_utterance_with_internal_pause()
    test_two_utterances()
    test_pre_roll_not_empty_on_short_speech()
    print("all vad tests passed")
