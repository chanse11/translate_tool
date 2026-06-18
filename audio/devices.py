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


def resolve_input_device(keyword: str = "") -> int | None:
    devices = list_input_devices()
    if keyword:
        found = find_device_by_keyword(devices, keyword)
        if found and found.index is not None:
            return found.index
    try:
        default = sd.default.device
        if isinstance(default, (tuple, list)) and default[0] >= 0:
            return int(default[0])
    except Exception:
        pass
    if devices:
        return devices[0].index
    return None


def get_default_output_device() -> int | None:
    """系统默认播放设备（耳机/扬声器），用于测试监听。"""
    try:
        default = sd.default.device
        if isinstance(default, (tuple, list)) and default[1] >= 0:
            return int(default[1])
    except Exception:
        pass
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
    try:
        default = sd.default.device
        if isinstance(default, (tuple, list)) and default[1] >= 0:
            return int(default[1])
    except Exception:
        pass
    return None
