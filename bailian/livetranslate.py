"""阿里云百炼 Qwen LiveTranslate WebSocket 会话。"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "qwen3.5-livetranslate-flash-realtime"
DEFAULT_WS_BASE = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
ASR_MODEL = "qwen3-asr-flash-realtime"


@dataclass
class TurnResult:
    """一轮同传结果。"""

    source_text: str = ""
    translation_text: str = ""
    audio_pcm16: bytes = b""
    audio_sample_rate: int = 24000


@dataclass
class _TurnState:
    source_text: str = ""
    translation_text: str = ""
    audio_chunks: list[bytes] = field(default_factory=list)


def build_ws_url(
    *,
    ws_base: str = "",
    workspace_id: str = "",
    model: str = DEFAULT_MODEL,
    region: str = "cn-beijing",
) -> str:
    """构造 LiveTranslate WebSocket URL。"""
    model = model or DEFAULT_MODEL
    workspace_id = (workspace_id or "").strip()
    ws_base = (ws_base or "").strip()

    if workspace_id:
        base = f"wss://{workspace_id}.{region}.maas.aliyuncs.com/api-ws/v1/realtime"
    elif ws_base:
        base = ws_base.rstrip("?")
    else:
        base = DEFAULT_WS_BASE

    sep = "&" if "?" in base else "?"
    if "model=" in base:
        return base
    return f"{base}{sep}model={model}"


class BailianLiveTranslateSession:
    """在后台线程维护一条 LiveTranslate WebSocket 长连接。"""

    def __init__(
        self,
        api_key: str,
        source_language: str,
        target_language: str,
        *,
        model: str = DEFAULT_MODEL,
        ws_base: str = "",
        workspace_id: str = "",
        region: str = "cn-beijing",
        audio_output: bool = True,
        enable_source_asr: bool = True,
        voice: str | None = None,
        enable_voice_clone: bool = False,
        voice_clone_frequency: str | None = None,
        silence_duration_ms: int = 1000,
        vad_threshold: float = 0.2,
        prefix_padding_ms: int = 500,
        # True=按住说话 Manual（turn_detection=null + commit）；False=服务端 VAD
        manual_turn_detection: bool = False,
        on_turn: Callable[[TurnResult], None] | None = None,
        on_partial: Callable[[str, str], None] | None = None,
        on_audio_delta: Callable[[bytes, int], None] | None = None,
        on_status: Callable[[str], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        if not api_key or not api_key.strip():
            raise ValueError("百炼 API Key 不能为空")

        self._api_key = api_key.strip()
        self._source_language = source_language
        self._target_language = target_language
        self._model = model or DEFAULT_MODEL
        self._ws_url = build_ws_url(
            ws_base=ws_base,
            workspace_id=workspace_id,
            model=self._model,
            region=region,
        )
        self._audio_output = audio_output
        self._enable_source_asr = enable_source_asr
        self._voice = (voice or "").strip()
        self._enable_voice_clone = enable_voice_clone
        self._voice_clone_frequency = (voice_clone_frequency or "").strip()
        # 服务端 VAD：官方默认静音 1000ms / 阈值 0.2，优先保证整句，不抢低延迟
        self._silence_duration_ms = int(
            max(200, min(6000, silence_duration_ms or 1000))
        )
        self._vad_threshold = float(max(-1.0, min(1.0, vad_threshold)))
        # 语音开始前多留一段，避免「你/我」等句首被 VAD 切掉
        self._prefix_padding_ms = int(max(0, min(2000, prefix_padding_ms or 500)))
        self._manual_turn_detection = bool(manual_turn_detection)
        self._on_turn = on_turn
        self._on_partial = on_partial
        self._on_audio_delta = on_audio_delta
        self._on_status = on_status
        self._on_error = on_error

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ws = None
        self._ready = threading.Event()
        self._stop_requested = threading.Event()
        self._connected = False
        # 队列元素：PCM bytes | 控制指令 "clear"/"commit" | None(结束)
        self._send_queue: asyncio.Queue[bytes | str | None] | None = None
        self._turn = _TurnState()
        self._lock = threading.Lock()
        self._queued_bytes = 0
        self._queued_lock = threading.Lock()
        self._queue_idle = threading.Event()
        self._queue_idle.set()
        # 连接完成前先缓存约 1.2 秒，避免持续拾取时句首被丢掉
        self._preroll: list[bytes] = []
        self._preroll_lock = threading.Lock()
        self._preroll_limit = 16000 * 2 * 12 // 10
        self._session_updated_async: asyncio.Event | None = None
        # 仅当「当前句已说完」且「上一句译文仍在生成」时才暂存。
        # 该模型经常先 response.done 再 speech_stopped，若只听 stopped 会永远卡住。
        self._hold_lock = threading.Lock()
        self._response_hold = False
        self._response_active = False
        self._speech_open = False
        self._hold_chunks: list[bytes] = []
        self._hold_limit = 16000 * 2 * 8  # 约 8 秒 PCM16
        self._hold_watchdog_token = 0

    @property
    def is_connected(self) -> bool:
        return self._connected

    def start(self, timeout: float = 20.0) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_requested.clear()
        self._ready.clear()
        self._thread = threading.Thread(target=self._run_thread, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=timeout):
            self.stop()
            raise TimeoutError("连接阿里百炼 LiveTranslate 超时，请检查 API Key / 网络 / 业务空间 ID")
        if not self._connected:
            raise RuntimeError("未能连接阿里百炼 LiveTranslate")

    def stop(self) -> None:
        self._stop_requested.set()
        loop = self._loop
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(self._request_shutdown(), loop)
        if self._thread:
            self._thread.join(timeout=8)
            self._thread = None
        self._connected = False
        self._loop = None
        with self._hold_lock:
            self._response_hold = False
            self._response_active = False
            self._speech_open = False
            self._hold_chunks = []
            self._hold_watchdog_token += 1

    def send_float32(self, samples: np.ndarray) -> None:
        """发送 float32 mono 16kHz 音频。未就绪时先缓存，避免句首丢失。"""
        if samples is None or len(samples) == 0:
            return
        pcm = np.clip(samples, -1.0, 1.0)
        pcm16 = (pcm * 32767.0).astype(np.int16).tobytes()
        self.send_pcm16(pcm16)

    def send_pcm16(self, pcm16: bytes) -> None:
        if not pcm16:
            return
        with self._preroll_lock:
            live = (
                self._connected
                and self._send_queue is not None
                and self._loop is not None
            )
            if not live:
                self._preroll.append(pcm16)
                total = sum(len(x) for x in self._preroll)
                while total > self._preroll_limit and self._preroll:
                    total -= len(self._preroll.pop(0))
                return
        with self._hold_lock:
            if self._response_hold:
                self._hold_chunks.append(pcm16)
                total = sum(len(x) for x in self._hold_chunks)
                while total > self._hold_limit and self._hold_chunks:
                    total -= len(self._hold_chunks.pop(0))
                return
        self._enqueue_pcm(pcm16)

    def _maybe_begin_response_hold(self, reason: str) -> None:
        """仅在译文仍在生成、且当前句已经结束后暂存，避免永远不再上送。"""
        with self._hold_lock:
            if self._response_hold:
                return
            if not self._response_active or self._speech_open:
                return
            self._response_hold = True
            self._hold_watchdog_token += 1
            token = self._hold_watchdog_token
        logger.info("上一句生成中，暂存麦克风输入 (%s)", reason)
        self._arm_hold_watchdog(token)

    def _flush_response_hold(self) -> None:
        with self._hold_lock:
            self._response_hold = False
            self._hold_watchdog_token += 1
            pending = self._hold_chunks
            self._hold_chunks = []
        if not pending:
            return
        total = sum(len(x) for x in pending)
        logger.info("上一句已结束，补发暂存音频 bytes=%s (~%.2fs)", total, total / 32000.0)
        for chunk in pending:
            self._enqueue_pcm(chunk)

    def _arm_hold_watchdog(self, token: int) -> None:
        loop = self._loop
        if loop is None:
            return

        def _fire() -> None:
            with self._hold_lock:
                if token != self._hold_watchdog_token or not self._response_hold:
                    return
            logger.warning("暂存超时，恢复上送麦克风")
            self._flush_response_hold()

        try:
            loop.call_later(2.0, _fire)
        except Exception:
            logger.debug("无法启动暂存超时保护", exc_info=True)

    def _mark_speech_open(self, opened: bool) -> None:
        with self._hold_lock:
            self._speech_open = opened

    def _mark_response_active(self, active: bool) -> None:
        with self._hold_lock:
            self._response_active = active

    def _enqueue_pcm(self, pcm16: bytes) -> None:
        if self._send_queue is None or self._loop is None:
            return
        with self._queued_lock:
            self._queued_bytes += len(pcm16)
            self._queue_idle.clear()
        try:
            asyncio.run_coroutine_threadsafe(self._send_queue.put(pcm16), self._loop)
        except Exception as exc:
            with self._queued_lock:
                self._queued_bytes = max(0, self._queued_bytes - len(pcm16))
                if self._queued_bytes == 0:
                    self._queue_idle.set()
            logger.debug("入队音频失败: %s", exc)

    def clear_audio_buffer(self) -> None:
        """清空服务端未提交音频（Manual 模式按住说话按下时用）。"""
        self._enqueue_control("clear")

    def commit_audio(self, *, wait_flush: bool = True, timeout: float = 2.0) -> None:
        """提交音频缓冲区并触发翻译（仅 Manual 模式）。

        wait_flush=True 时先等本地发送队列排空，避免 commit 抢在尾包前面。
        """
        if wait_flush:
            self.wait_send_idle(timeout=timeout)
        self._enqueue_control("commit")

    def wait_send_idle(self, timeout: float = 2.0) -> bool:
        """等待本地音频发送队列排空。"""
        return self._queue_idle.wait(timeout=timeout)

    def _enqueue_control(self, cmd: str) -> None:
        if not self._connected or self._send_queue is None or self._loop is None:
            return
        with self._queued_lock:
            self._queued_bytes += 1  # 控制指令也占位，保证 idle 语义
            self._queue_idle.clear()
        try:
            asyncio.run_coroutine_threadsafe(self._send_queue.put(cmd), self._loop)
        except Exception as exc:
            with self._queued_lock:
                self._queued_bytes = max(0, self._queued_bytes - 1)
                if self._queued_bytes == 0:
                    self._queue_idle.set()
            logger.debug("入队控制指令失败 (%s): %s", cmd, exc)

    def _mark_sent(self, item: bytes | str) -> None:
        dec = len(item) if isinstance(item, (bytes, bytearray)) else 1
        with self._queued_lock:
            self._queued_bytes = max(0, self._queued_bytes - dec)
            if self._queued_bytes == 0:
                self._queue_idle.set()

    def _set_status(self, msg: str) -> None:
        if self._on_status:
            self._on_status(msg)

    def _emit_error(self, msg: str) -> None:
        logger.error(msg)
        if self._on_error:
            self._on_error(msg)

    def _run_thread(self) -> None:
        try:
            import websockets  # noqa: F401
        except ImportError:
            self._emit_error("缺少依赖 websockets，请执行: pip install websockets")
            self._ready.set()
            return

        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._main())
        except Exception as exc:
            logger.exception("百炼会话异常退出: %s", exc)
            self._emit_error(str(exc))
        finally:
            self._connected = False
            self._ready.set()
            try:
                loop.close()
            except Exception:
                pass

    async def _request_shutdown(self) -> None:
        if self._send_queue is not None:
            await self._send_queue.put(None)

    async def _main(self) -> None:
        import websockets

        headers = {"Authorization": f"Bearer {self._api_key}"}
        self._set_status("正在连接阿里百炼…")
        logger.info("连接 LiveTranslate: %s", self._ws_url)

        try:
            try:
                ws_ctx = websockets.connect(
                    self._ws_url,
                    additional_headers=headers,
                    max_size=8 * 1024 * 1024,
                    ping_interval=20,
                    ping_timeout=20,
                )
            except TypeError:
                ws_ctx = websockets.connect(
                    self._ws_url,
                    extra_headers=headers,
                    max_size=8 * 1024 * 1024,
                    ping_interval=20,
                    ping_timeout=20,
                )
            async with ws_ctx as ws:
                self._ws = ws
                self._send_queue = asyncio.Queue()
                self._session_updated_async = asyncio.Event()
                receiver = asyncio.create_task(self._receive_loop())
                await self._configure_session()
                try:
                    await asyncio.wait_for(self._session_updated_async.wait(), 8)
                except asyncio.TimeoutError:
                    logger.warning("未收到 session.updated，继续发送音频")
                sender = asyncio.create_task(self._send_loop())
                await self._flush_preroll()
                with self._preroll_lock:
                    extra = self._preroll
                    self._preroll = []
                    self._connected = True
                await self._enqueue_pcm_list(extra)
                self._ready.set()
                self._set_status("百炼同传已连接")

                stopper = asyncio.create_task(self._watch_stop())
                done, pending = await asyncio.wait(
                    {receiver, sender, stopper},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                await self._graceful_finish()
        except Exception as exc:
            self._connected = False
            self._ready.set()
            self._emit_error(f"百炼连接失败: {exc}")
            raise
        finally:
            self._ws = None
            self._send_queue = None
            self._connected = False

    async def _watch_stop(self) -> None:
        while not self._stop_requested.is_set():
            await asyncio.sleep(0.1)
        if self._send_queue is not None:
            await self._send_queue.put(None)

    async def _configure_session(self) -> None:
        modalities = ["text", "audio"] if self._audio_output else ["text"]
        session: dict = {
            "modalities": modalities,
            "input_audio_format": "pcm",
            "output_audio_format": "pcm",
            "translation": {
                "language": self._target_language,
            },
        }
        if self._voice:
            session["voice"] = self._voice
        if self._enable_voice_clone:
            session["enable_voice_clone"] = True
            if self._voice_clone_frequency:
                session["voice_clone_options"] = {
                    "frequency": self._voice_clone_frequency,
                }
        if self._enable_source_asr:
            session["input_audio_transcription"] = {
                "model": ASR_MODEL,
                "language": self._source_language,
            }
        else:
            session["input_audio_transcription"] = {
                "language": self._source_language,
            }
        silence_ms = 0
        if self._manual_turn_detection:
            session["turn_detection"] = None
        else:
            silence_ms = self._silence_duration_ms
            session["turn_detection"] = {
                "type": "server_vad",
                "threshold": self._vad_threshold,
                "prefix_padding_ms": self._prefix_padding_ms,
                "silence_duration_ms": silence_ms,
            }
        session["sample_rate"] = 16000

        event = {
            "event_id": self._event_id(),
            "type": "session.update",
            "session": session,
        }
        await self._ws.send(json.dumps(event, ensure_ascii=False))
        logger.info(
            "已配置会话: %s→%s modalities=%s turn=%s silence_ms=%s prefix_ms=%s "
            "voice=%s clone=%s freq=%s",
            self._source_language,
            self._target_language,
            modalities,
            "manual" if self._manual_turn_detection else "server_vad",
            silence_ms,
            self._prefix_padding_ms,
            self._voice or "",
            self._enable_voice_clone,
            self._voice_clone_frequency or "",
        )

    async def _flush_preroll(self) -> None:
        with self._preroll_lock:
            pending = self._preroll
            self._preroll = []
        await self._enqueue_pcm_list(pending)

    async def _enqueue_pcm_list(self, chunks: list[bytes]) -> None:
        if not chunks or self._send_queue is None:
            return
        total = sum(len(x) for x in chunks)
        logger.info("补发连接前音频 chunks=%s bytes=%s", len(chunks), total)
        for chunk in chunks:
            with self._queued_lock:
                self._queued_bytes += len(chunk)
                self._queue_idle.clear()
            await self._send_queue.put(chunk)

    async def _send_loop(self) -> None:
        assert self._send_queue is not None
        while True:
            chunk = await self._send_queue.get()
            if chunk is None or self._stop_requested.is_set():
                break
            if not self._ws:
                break
            if isinstance(chunk, str):
                if chunk == "clear":
                    event = {
                        "event_id": self._event_id(),
                        "type": "input_audio_buffer.clear",
                    }
                elif chunk == "commit":
                    event = {
                        "event_id": self._event_id(),
                        "type": "input_audio_buffer.commit",
                    }
                else:
                    logger.warning("未知控制指令: %s", chunk)
                    self._mark_sent(chunk)
                    continue
            else:
                event = {
                    "event_id": self._event_id(),
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(chunk).decode("ascii"),
                }
            try:
                await self._ws.send(json.dumps(event))
            except Exception as exc:
                self._emit_error(f"发送音频失败: {exc}")
                self._mark_sent(chunk)
                break
            self._mark_sent(chunk)

    async def _receive_loop(self) -> None:
        assert self._ws is not None
        async for message in self._ws:
            if self._stop_requested.is_set():
                break
            try:
                if isinstance(message, bytes):
                    message = message.decode("utf-8")
                event = json.loads(message)
            except Exception:
                logger.warning("无法解析服务端消息")
                continue
            self._handle_event(event)

    def _handle_event(self, event: dict) -> None:
        event_type = event.get("type", "")

        if event_type == "error":
            err = event.get("error") or event
            msg = err.get("message") if isinstance(err, dict) else str(err)
            self._mark_response_active(False)
            self._flush_response_hold()
            self._emit_error(f"百炼错误: {msg}")
            return

        if event_type == "session.updated":
            sess = event.get("session") or {}
            logger.info(
                "百炼会话已更新: voice=%s enable_voice_clone=%s clone_opts=%s turn=%s",
                sess.get("voice"),
                sess.get("enable_voice_clone"),
                sess.get("voice_clone_options"),
                sess.get("turn_detection"),
            )
            clone_on = bool(sess.get("enable_voice_clone"))
            if clone_on:
                self._set_status("百炼会话已就绪（音色复刻已开启）")
            else:
                self._set_status("百炼会话已就绪")
            if self._session_updated_async is not None:
                self._session_updated_async.set()
            return

        if event_type == "input_audio_buffer.speech_started":
            logger.info("服务端检测到语音开始")
            self._mark_speech_open(True)
            return

        if event_type == "input_audio_buffer.speech_stopped":
            logger.info("服务端检测到语音结束")
            self._mark_speech_open(False)
            self._maybe_begin_response_hold("speech_stopped")
            return

        if event_type == "input_audio_buffer.committed":
            logger.info("音频缓冲区已提交，等待翻译…")
            self._mark_speech_open(False)
            self._maybe_begin_response_hold("committed")
            return

        if event_type == "response.created":
            logger.info("服务端开始生成翻译")
            self._mark_response_active(True)
            self._maybe_begin_response_hold("response.created")
            return

        if event_type == "conversation.item.input_audio_transcription.completed":
            text = (event.get("transcript") or "").strip()
            if text:
                with self._lock:
                    self._turn.source_text = text
                self._emit_partial()
            return

        if event_type == "conversation.item.input_audio_transcription.text":
            text = (event.get("transcript") or event.get("text") or "").strip()
            stash = (event.get("stash") or "").strip()
            combined = (text + stash).strip()
            if combined:
                with self._lock:
                    self._turn.source_text = combined
                self._emit_partial()
            return

        if event_type in ("response.audio_transcript.text", "response.text.text"):
            text = (event.get("transcript") or event.get("text") or "").strip()
            stash = (event.get("stash") or "").strip()
            combined = (text + stash).strip()
            if combined:
                with self._lock:
                    self._turn.translation_text = combined
                self._emit_partial()
            return

        if event_type == "response.audio_transcript.done":
            text = (event.get("transcript") or "").strip()
            if text:
                with self._lock:
                    self._turn.translation_text = text
                self._emit_partial()
            return

        if event_type == "response.text.done":
            text = (event.get("text") or "").strip()
            if text:
                with self._lock:
                    self._turn.translation_text = text
                self._emit_partial()
            return

        if event_type == "response.audio.delta":
            audio_b64 = event.get("delta") or event.get("audio") or ""
            if audio_b64:
                try:
                    chunk = base64.b64decode(audio_b64)
                except Exception:
                    return
                self._mark_response_active(True)
                with self._lock:
                    self._turn.audio_chunks.append(chunk)
                if self._on_audio_delta and chunk:
                    try:
                        self._on_audio_delta(chunk, 24000)
                    except Exception:
                        logger.exception("音频分片回调失败")
            return

        if event_type == "response.done":
            self._mark_response_active(False)
            self._finalize_turn()
            self._flush_response_hold()
            return

        if event_type == "session.finished":
            logger.info("百炼会话已结束")
            return

    def _emit_partial(self) -> None:
        if not self._on_partial:
            return
        with self._lock:
            source = self._turn.source_text.strip()
            translation = self._turn.translation_text.strip()
        if not source and not translation:
            return
        try:
            self._on_partial(source, translation)
        except Exception:
            logger.exception("流式字幕回调失败")

    def _finalize_turn(self) -> None:
        with self._lock:
            result = TurnResult(
                source_text=self._turn.source_text.strip(),
                translation_text=self._turn.translation_text.strip(),
                audio_pcm16=b"".join(self._turn.audio_chunks),
                audio_sample_rate=24000,
            )
            self._turn = _TurnState()

        logger.info(
            "一轮完成: zh=%r en=%r audio_bytes=%s clone=%s freq=%s",
            (result.source_text[:80] if result.source_text else ""),
            (result.translation_text[:80] if result.translation_text else ""),
            len(result.audio_pcm16),
            self._enable_voice_clone,
            self._voice_clone_frequency or "",
        )

        if not result.translation_text and not result.audio_pcm16:
            return
        if self._on_turn:
            try:
                self._on_turn(result)
            except Exception:
                logger.exception("处理同传结果回调失败")

    async def _graceful_finish(self) -> None:
        if not self._ws:
            return
        try:
            finish_event = {
                "event_id": self._event_id(),
                "type": "session.finish",
            }
            await self._ws.send(json.dumps(finish_event))
            # 尽量收尾，不无限等待
            await asyncio.sleep(0.3)
        except Exception as exc:
            logger.debug("发送 session.finish 失败: %s", exc)

    @staticmethod
    def _event_id() -> str:
        return f"event_{uuid.uuid4().hex}"
