"""中英文启发式语种检测（零依赖）。"""

from __future__ import annotations

import re

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")
_LETTER_RE = re.compile(r"[A-Za-z]")


def detect_zh_or_en(text: str, *, cjk_ratio_threshold: float = 0.2) -> str:
    """根据 CJK 字符占比判断语种，返回 ``\"zh\"`` 或 ``\"en\"``。

    混合或无法判断时默认 ``\"zh\"``（按中→英翻译）。
    """
    s = (text or "").strip()
    if not s:
        return "zh"

    cjk = len(_CJK_RE.findall(s))
    letters = len(_LETTER_RE.findall(s))
    total = cjk + letters
    if total == 0:
        return "zh"
    if cjk / total >= cjk_ratio_threshold:
        return "zh"
    return "en"
