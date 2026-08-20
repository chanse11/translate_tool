"""按需创建 pipeline，避免启动时加载 sounddevice / Whisper 等重依赖。"""

from __future__ import annotations

from typing import Any, Callable


def create_en_to_zh_pipeline(
    backend: str,
    config: dict,
    *,
    resources: Any | None,
    api_key: str,
    on_subtitle: Callable,
    on_status: Callable | None,
    is_paused: Callable,
    tts_enabled: Callable,
) -> Any:
    if backend == "bailian":
        from pipeline.bailian_en_to_zh import BailianEnToZhPipeline

        return BailianEnToZhPipeline(
            config,
            api_key,
            on_subtitle=on_subtitle,
            on_status=on_status,
            is_paused=is_paused,
            tts_enabled=tts_enabled,
        )

    from pipeline.en_to_zh import EnToZhPipeline

    if resources is None:
        raise RuntimeError("本地资源尚未初始化")
    return EnToZhPipeline(
        config,
        resources,
        on_subtitle=on_subtitle,
        on_status=on_status,
        is_paused=is_paused,
        tts_enabled=tts_enabled,
    )


def create_zh_to_en_pipeline(
    backend: str,
    config: dict,
    *,
    resources: Any | None,
    api_key: str,
    on_result: Callable,
    on_status: Callable | None,
    on_tts_start: Callable | None,
    on_tts_end: Callable | None,
    is_test_mode: Callable,
) -> Any:
    if backend == "bailian":
        from pipeline.bailian_zh_to_en import BailianZhToEnPipeline

        return BailianZhToEnPipeline(
            config,
            api_key,
            on_result=on_result,
            on_status=on_status,
            on_tts_start=on_tts_start,
            on_tts_end=on_tts_end,
            is_test_mode=is_test_mode,
        )

    from pipeline.zh_to_en import ZhToEnPipeline

    if resources is None:
        raise RuntimeError("本地资源尚未初始化")
    return ZhToEnPipeline(
        config,
        resources,
        on_result=on_result,
        on_status=on_status,
        on_tts_start=on_tts_start,
        on_tts_end=on_tts_end,
        is_test_mode=is_test_mode,
    )


def create_app_resources(config: dict) -> Any:
    from pipeline.resources import AppResources

    return AppResources(config)
