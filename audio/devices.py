"""音频设备枚举与选择。"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pyaudiowpatch as pyaudio
import sounddevice as sd

logger = logging.getLogger(__name__)


@dataclass
class AudioDeviceInfo:
    name: str
    index: int | None = None
    hostapi: str = ""
    is_loopback: bool = False


def list_output_devices() -> list[AudioDeviceInfo]:
    """列出 sounddevice 输出设备。"""
    devices: list[AudioDeviceInfo] = []
    for i, dev in enumerate(sd.query_devices()):
        if dev["max_output_channels"] > 0:
            hostapis = sd.query_hostapis()
            host_name = hostapis[dev["hostapi"]]["name"]
            devices.append(
                AudioDeviceInfo(name=dev["name"], index=i, hostapi=host_name)
            )
    return devices


def list_input_devices() -> list[AudioDeviceInfo]:
    """列出 sounddevice 输入设备。"""
    devices: list[AudioDeviceInfo] = []
    for i, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0:
            hostapis = sd.query_hostapis()
            host_name = hostapis[dev["hostapi"]]["name"]
            devices.append(
                AudioDeviceInfo(name=dev["name"], index=i, hostapi=host_name)
            )
    return devices


def find_device_by_keyword(
    devices: list[AudioDeviceInfo], keyword: str
) -> AudioDeviceInfo | None:
    if not keyword:
        return None
    keyword_lower = keyword.lower()
    for dev in devices:
        if keyword_lower in dev.name.lower():
            return dev
    return None


def get_default_loopback_device() -> dict | None:
    """通过 pyaudiowpatch 获取默认 WASAPI 环回设备。"""
    try:
        pa = pyaudio.PyAudio()
        try:
            wasapi_info = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            default_speakers = pa.get_device_info_by_index(
                wasapi_info["defaultOutputDevice"]
            )
            if not default_speakers.get("isLoopbackDevice", False):
                for loopback in pa.get_loopback_device_info_generator():
                    if default_speakers["name"] in loopback["name"]:
                        logger.info("环回设备: %s", loopback["name"])
                        return loopback
            logger.info("环回设备: %s", default_speakers["name"])
            return default_speakers
        finally:
            pa.terminate()
    except Exception as exc:
        logger.error("获取环回设备失败: %s", exc)
        return None


def resolve_loopback_device(keyword: str = "") -> dict | None:
    if keyword:
        pa = pyaudio.PyAudio()
        try:
            for loopback in pa.get_loopback_device_info_generator():
                if keyword.lower() in loopback["name"].lower():
                    return loopback
        finally:
            pa.terminate()
    return get_default_loopback_device()


def _default_io_indices() -> tuple[int | None, int | None]:
    """读取 sounddevice 默认输入/输出索引。

    注意：``sd.default.device`` 实际类型是 ``_InputOutputPair``，
    不是 list/tuple；用 isinstance 判断会失败并误选设备 0（Sound Mapper）。
    """
    try:
        default = sd.default.device
        in_idx = int(default[0]) if default[0] is not None and int(default[0]) >= 0 else None
        out_idx = int(default[1]) if default[1] is not None and int(default[1]) >= 0 else None
        return in_idx, out_idx
    except Exception:
        return None, None


def _is_mapper_device(name: str) -> bool:
    lower = (name or "").lower()
    return "sound mapper" in lower or "primary sound" in lower


def _is_virtual_cable_input(name: str) -> bool:
    lower = (name or "").lower()
    return (
        "cable output" in lower
        or "vb-audio" in lower
        or "voicemeeter" in lower
        or "virtual cable" in lower
    )


def _is_usable_mic(name: str) -> bool:
    return not _is_mapper_device(name) and not _is_virtual_cable_input(name)


def resolve_input_device(keyword: str = "") -> int | None:
    devices = list_input_devices()
    if keyword:
        found = find_device_by_keyword(devices, keyword)
        if found and found.index is not None:
            return found.index
    in_idx, _ = _default_io_indices()
    if in_idx is not None:
        try:
            info = sd.query_devices(in_idx)
            name = info.get("name", "") if isinstance(info, dict) else str(info)
        except Exception:
            name = ""
        # 桌面版实际走系统默认/Mapper，能采到插孔麦。不要因为 Mapper 就改选别的设备。
        if not _is_virtual_cable_input(name):
            return in_idx
        logger.warning("系统默认输入是虚拟线缆（%s），改选物理麦克风", name or in_idx)
    for dev in devices:
        if dev.index is not None and _is_usable_mic(dev.name):
            return dev.index
    if devices:
        return devices[0].index
    return None


def get_default_output_device() -> int | None:
    """系统默认播放设备（耳机/扬声器），用于测试监听。"""
    _, out_idx = _default_io_indices()
    if out_idx is not None:
        return out_idx
    devices = list_output_devices()
    if devices:
        return devices[0].index
    return None


def resolve_output_device(keyword: str = "") -> int | None:
    devices = list_output_devices()
    if keyword:
        found = find_device_by_keyword(devices, keyword)
        if found and found.index is not None:
            return found.index
    _, out_idx = _default_io_indices()
    return out_idx
