"""将文本按标点切分为适合逐句 TTS 的片段。"""

from __future__ import annotations

import re

# 中英文句末及停顿标点
_SPLIT_RE = re.compile(r"([。！？；.!?;]+)")


def split_for_tts(text: str, min_chars: int = 4) -> list[str]:
    """按标点切分文本，过短片段会合并到下一段。"""
    text = text.strip()
    if not text:
        return []

    parts = _SPLIT_RE.split(text)
    chunks: list[str] = []
    current = ""

    for part in parts:
        if not part:
            continue
        if _SPLIT_RE.fullmatch(part):
            current += part
            if current.strip():
                chunks.append(current.strip())
            current = ""
        else:
            if current and len(current.strip()) >= min_chars:
                chunks.append(current.strip())
                current = part
            else:
                current += part

    if current.strip():
        chunks.append(current.strip())

    if not chunks:
        return [text]

    # 合并过短的尾段
    merged: list[str] = []
    for chunk in chunks:
        if merged and len(chunk) < min_chars:
            merged[-1] = merged[-1] + chunk
        else:
            merged.append(chunk)
    return merged
