"""阿里云百炼文本对话（DashScope OpenAI 兼容模式）。"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import httpx

logger = logging.getLogger(__name__)

DEFAULT_CHAT_URL = (
    "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
)
DEFAULT_MODEL = "qwen-plus"
DEFAULT_SYSTEM_PROMPT = (
    "你是会议助手。根据对方刚说的中文内容，给出简洁、可直接在会上使用的回复建议。"
)


def _prepare_request(
    api_key: str,
    user_text: str,
    *,
    model: str,
    system_prompt: str,
    stream: bool,
) -> tuple[dict, dict]:
    key = (api_key or "").strip()
    if not key:
        raise ValueError("请先填写阿里百炼 API Key")
    text = (user_text or "").strip()
    if not text:
        raise ValueError("发送内容为空")

    model_name = (model or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    system = (system_prompt or "").strip() or DEFAULT_SYSTEM_PROMPT

    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": text},
        ],
        "temperature": 0.7,
        "stream": stream,
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    return payload, headers


def _raise_http_error(resp: httpx.Response) -> None:
    detail = resp.text[:300]
    try:
        err = resp.json()
        detail = (
            err.get("error", {}).get("message")
            or err.get("message")
            or detail
        )
    except Exception:
        pass
    raise RuntimeError(f"大模型请求失败 ({resp.status_code}): {detail}")


def chat_completion(
    api_key: str,
    user_text: str,
    *,
    model: str = DEFAULT_MODEL,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    timeout: float = 60.0,
    base_url: str = DEFAULT_CHAT_URL,
) -> str:
    """调用百炼 Chat Completions，返回完整助手文本。"""
    parts: list[str] = []
    for chunk in chat_completion_stream(
        api_key,
        user_text,
        model=model,
        system_prompt=system_prompt,
        timeout=timeout,
        base_url=base_url,
    ):
        parts.append(chunk)
    content = "".join(parts).strip()
    if not content:
        raise RuntimeError("大模型返回空内容")
    return content


def chat_completion_stream(
    api_key: str,
    user_text: str,
    *,
    model: str = DEFAULT_MODEL,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    timeout: float = 90.0,
    base_url: str = DEFAULT_CHAT_URL,
) -> Iterator[str]:
    """流式调用百炼 Chat Completions，逐段 yield 文本增量。"""
    payload, headers = _prepare_request(
        api_key,
        user_text,
        model=model,
        system_prompt=system_prompt,
        stream=True,
    )

    try:
        with httpx.Client(timeout=timeout) as client:
            with client.stream(
                "POST", base_url, headers=headers, json=payload
            ) as resp:
                if resp.status_code >= 400:
                    # 读完 body 才能解析错误信息
                    _ = resp.read()
                    _raise_http_error(resp)

                for line in resp.iter_lines():
                    if not line:
                        continue
                    if line.startswith("data:"):
                        data_str = line[5:].strip()
                    else:
                        data_str = line.strip()
                    if not data_str or data_str == "[DONE]":
                        if data_str == "[DONE]":
                            break
                        continue
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        logger.debug("跳过非 JSON 流片段: %s", data_str[:80])
                        continue

                    if "error" in data:
                        err = data["error"]
                        msg = (
                            err.get("message")
                            if isinstance(err, dict)
                            else str(err)
                        )
                        raise RuntimeError(f"大模型流式错误: {msg}")

                    try:
                        delta = data["choices"][0].get("delta") or {}
                        content = delta.get("content") or ""
                    except (KeyError, IndexError, TypeError):
                        continue
                    if content:
                        yield content
    except httpx.TimeoutException as exc:
        raise RuntimeError("大模型请求超时，请稍后重试") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"网络错误: {exc}") from exc
