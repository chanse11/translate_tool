"""诊断 LiveTranslate 声音复刻：录音 → 提交 → 保存输入/输出 WAV，打印全部事件。"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import time
import uuid
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from audio.devices import resolve_input_device  # noqa: E402


def _event_id() -> str:
    return f"event_{uuid.uuid4().hex[:16]}"


def _save_wav(path: Path, pcm16: bytes, sr: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm16)


def _rms(pcm16: bytes) -> float:
    if not pcm16:
        return 0.0
    x = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(x * x)) / 32768.0)


async def main() -> None:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    api_key = (cfg.get("bailian") or {}).get("api_key", "").strip()
    model = (cfg.get("bailian") or {}).get(
        "model", "qwen3.5-livetranslate-flash-realtime"
    )
    if not api_key:
        raise SystemExit("config.yaml 缺少 bailian.api_key")

    mic = resolve_input_device((cfg.get("audio") or {}).get("microphone_device", ""))
    info = sd.query_devices(mic)
    print(f"麦克风: device={mic} name={info['name']}")

    seconds = 5.0
    sr = 16000
    print(f"请对着麦克风说中文 {seconds:.0f} 秒…")
    audio = sd.rec(
        int(seconds * sr),
        samplerate=sr,
        channels=1,
        dtype="float32",
        device=mic,
    )
    sd.wait()
    mono = audio.reshape(-1)
    pcm16 = (np.clip(mono, -1, 1) * 32767.0).astype(np.int16).tobytes()
    out_dir = ROOT / "data" / "clone_diag"
    _save_wav(out_dir / "input_zh.wav", pcm16, sr)
    print(f"输入已保存 input_zh.wav 时长={seconds}s rms={_rms(pcm16):.4f}")

    import websockets

    url = f"wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model={model}"
    headers = {"Authorization": f"Bearer {api_key}"}

    # 尽量贴近官方复刻示例 + Manual
    session = {
        "modalities": ["text", "audio"],
        "voice": "default",
        "enable_voice_clone": True,
        "voice_clone_options": {"frequency": "always"},
        "sample_rate": 16000,
        "input_audio_format": "pcm",
        "output_audio_format": "pcm",
        "turn_detection": None,
        "input_audio_transcription": {
            "model": "qwen3-asr-flash-realtime",
            "language": "zh",
        },
        "translation": {"language": "en"},
    }

    out_chunks: list[bytes] = []
    events: list[str] = []

    try:
        ws_ctx = websockets.connect(
            url, additional_headers=headers, max_size=8 * 1024 * 1024
        )
    except TypeError:
        ws_ctx = websockets.connect(
            url, extra_headers=headers, max_size=8 * 1024 * 1024
        )

    async with ws_ctx as ws:
        await ws.send(
            json.dumps(
                {"event_id": _event_id(), "type": "session.update", "session": session},
                ensure_ascii=False,
            )
        )

        # 分片发送
        frame = 3200  # 100ms @16k int16
        for i in range(0, len(pcm16), frame):
            chunk = pcm16[i : i + frame]
            await ws.send(
                json.dumps(
                    {
                        "event_id": _event_id(),
                        "type": "input_audio_buffer.append",
                        "audio": base64.b64encode(chunk).decode("ascii"),
                    }
                )
            )
        await ws.send(
            json.dumps({"event_id": _event_id(), "type": "input_audio_buffer.commit"})
        )
        print("已 commit，等待响应…")

        deadline = time.time() + 45
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
            except asyncio.TimeoutError:
                if out_chunks:
                    break
                continue
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            ev = json.loads(raw)
            et = ev.get("type", "")
            events.append(et)
            if et == "session.updated":
                print("session.updated:", json.dumps(ev.get("session"), ensure_ascii=False)[:800])
            elif et == "error":
                print("ERROR:", ev)
                break
            elif et == "conversation.item.input_audio_transcription.completed":
                print("ASR:", ev.get("transcript"))
            elif et in ("response.audio_transcript.done", "response.text.done"):
                print("EN:", ev.get("transcript") or ev.get("text"))
            elif et == "response.audio.delta":
                b64 = ev.get("delta") or ""
                if b64:
                    out_chunks.append(base64.b64decode(b64))
            elif et == "response.done":
                print("response.done")
                break
            elif et == "input_audio_buffer.committed":
                print("committed")

        await ws.send(json.dumps({"event_id": _event_id(), "type": "session.finish"}))

    out_pcm = b"".join(out_chunks)
    _save_wav(out_dir / "output_en.wav", out_pcm, 24000)
    print(
        f"输出已保存 output_en.wav bytes={len(out_pcm)} rms={_rms(out_pcm):.4f} "
        f"events={events}"
    )
    print(f"请对比听: {out_dir}")


if __name__ == "__main__":
    asyncio.run(main())
