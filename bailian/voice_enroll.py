"""百炼声音复刻（预注册音色，供 LiveTranslate frequency=never 使用）。"""

from __future__ import annotations

import base64
import io
import json
import logging
import urllib.error
import urllib.request
import wave
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

ENROLL_URL = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization"


def speech_window_rms(samples: np.ndarray, sample_rate: int, win_ms: int = 200) -> float:
    """滑动窗最大 RMS，避免整段静音把说话音量平均掉。"""
    if samples is None or len(samples) == 0:
        return 0.0
    x = np.asarray(samples, dtype=np.float64).reshape(-1)
    win = max(1, int(sample_rate * win_ms / 1000))
    if len(x) < win:
        return float(np.sqrt(np.mean(x * x)))
    n = len(x) // win
    chunks = x[: n * win].reshape(n, win)
    rms = np.sqrt(np.mean(chunks * chunks, axis=1))
    return float(np.max(rms)) if len(rms) else 0.0


def normalize_speech(
    samples: np.ndarray, *, target_peak: float = 0.55, max_gain: float = 20.0
) -> np.ndarray:
    """轻量增益，让偏小声的采样也能用于复刻。"""
    x = np.asarray(samples, dtype=np.float32).reshape(-1)
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    if peak < 1e-6:
        return x
    gain = min(target_peak / peak, max_gain)
    if gain > 1.05:
        logger.info("录音增益 x%.2f (peak=%.4f → %.2f)", gain, peak, target_peak)
        x = x * gain
    return np.clip(x, -1.0, 1.0).astype(np.float32)


def resample_audio(
    samples: np.ndarray, src_rate: int, dst_rate: int
) -> np.ndarray:
    """线性重采样到目标采样率（官方复刻要求 ≥ 24 kHz）。"""
    x = np.asarray(samples, dtype=np.float32).reshape(-1)
    src_rate = int(src_rate)
    dst_rate = int(dst_rate)
    if src_rate <= 0 or dst_rate <= 0 or src_rate == dst_rate or len(x) == 0:
        return x
    n_dst = max(1, int(round(len(x) * dst_rate / src_rate)))
    src_t = np.linspace(0.0, 1.0, num=len(x), endpoint=False)
    dst_t = np.linspace(0.0, 1.0, num=n_dst, endpoint=False)
    return np.interp(dst_t, src_t, x).astype(np.float32)


def float32_to_wav_bytes(samples: np.ndarray, sample_rate: int = 16000) -> bytes:
    """float32 mono → WAV (pcm_s16le) bytes。"""
    pcm = np.clip(samples, -1.0, 1.0)
    pcm16 = (pcm * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(sample_rate))
        wf.writeframes(pcm16.tobytes())
    return buf.getvalue()


def _friendly_enroll_error(detail: str) -> str:
    text = detail or ""
    lower = text.lower()
    if "2038" in text or "无复刻权限" in text or "认证状态" in text:
        return (
            "账号还没有声音复刻权限。请确认：\n"
            "1）阿里云账号已实名认证；\n"
            "2）已开通阿里云百炼，且 API Key 属于同一账号；\n"
            "3）若走 CosyVoice，还需开通「流式文本语音合成」商用版。"
        )
    if "403" in text or "accessdenied" in lower or "permission" in lower:
        return f"没有复刻权限或 API Key 不对。原始信息：{text[:300]}"
    if "quota" in lower or "40001000" in text:
        return "配额/开通检查失败，请到百炼控制台确认已开通声音复刻相关服务。"
    return text[:500]


def create_livetranslate_voice(
    api_key: str,
    audio_wav_bytes: bytes,
    *,
    target_model: str = "qwen3.5-livetranslate-flash-realtime",
    preferred_name: str = "meeting",
    language: str = "zh",
    timeout: float = 120.0,
) -> str:
    """上传音频创建 LiveTranslate 专用音色，返回 voice_id。"""
    if not api_key or not api_key.strip():
        raise ValueError("API Key 为空")
    if not audio_wav_bytes:
        raise ValueError("音频为空")

    data_uri = "data:audio/wav;base64," + base64.b64encode(audio_wav_bytes).decode(
        "ascii"
    )
    payload: dict[str, Any] = {
        "model": "qwen-voice-enrollment",
        "input": {
            "action": "create",
            "target_model": target_model,
            "preferred_name": preferred_name[:16],
            "language": language,
            "audio": {"data": data_uri},
        },
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        ENROLL_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key.strip()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    logger.info(
        "创建复刻音色 target_model=%s preferred_name=%s wav_bytes=%s",
        target_model,
        preferred_name,
        len(audio_wav_bytes),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            status = getattr(resp, "status", 200)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(
            f"创建音色失败 HTTP {exc.code}: {_friendly_enroll_error(detail)}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"创建音色网络失败: {exc}") from exc

    if status != 200:
        raise RuntimeError(
            f"创建音色失败 HTTP {status}: {_friendly_enroll_error(raw[:800])}"
        )
    data = json.loads(raw)
    if isinstance(data, dict) and data.get("code"):
        msg = f"{data.get('code')} {data.get('message') or data}"
        raise RuntimeError(f"创建音色失败: {_friendly_enroll_error(msg)}")
    try:
        voice_id = data["output"]["voice"]
    except (KeyError, TypeError) as exc:
        raise RuntimeError(f"解析音色 ID 失败: {data}") from exc
    if not voice_id:
        raise RuntimeError(f"未返回音色 ID: {data}")
    fallback = (data.get("output") or {}).get("fallback_mode")
    if fallback:
        reason = (data.get("output") or {}).get("fallback_reason") or ""
        logger.warning("音色创建降级 fallback_mode=True reason=%s", reason)
    logger.info("复刻音色已创建: %s", voice_id)
    return str(voice_id)
