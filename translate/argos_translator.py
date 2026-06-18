"""Argos Translate 离线翻译。"""

from __future__ import annotations

import logging

import argostranslate.package
import argostranslate.translate

logger = logging.getLogger(__name__)

REQUIRED_PAIRS = [("en", "zh"), ("zh", "en")]


class ArgosTranslator:
    def __init__(self) -> None:
        self._ready = False

    @property
    def is_ready(self) -> bool:
        return self._ready

    def setup(self) -> None:
        """下载并安装所需语言包。"""
        logger.info("检查 Argos 翻译语言包…")
        argostranslate.package.update_package_index()
        available = argostranslate.package.get_available_packages()
        installed = argostranslate.translate.get_installed_languages()
        installed_codes = {lang.code for lang in installed}

        for from_code, to_code in REQUIRED_PAIRS:
            if self._pair_installed(from_code, to_code):
                logger.info("语言包已安装: %s -> %s", from_code, to_code)
                continue
            package = next(
                (
                    p
                    for p in available
                    if p.from_code == from_code and p.to_code == to_code
                ),
                None,
            )
            if package is None:
                raise RuntimeError(
                    f"找不到 Argos 语言包: {from_code} -> {to_code}"
                )
            logger.info("下载语言包: %s -> %s", from_code, to_code)
            download_path = package.download()
            argostranslate.package.install_from_path(download_path)
            argostranslate.translate.get_installed_languages.cache_clear()

        self._ready = True
        logger.info("Argos 翻译就绪")

    def _pair_installed(self, from_code: str, to_code: str) -> bool:
        installed = argostranslate.translate.get_installed_languages()
        from_lang = next((l for l in installed if l.code == from_code), None)
        to_lang = next((l for l in installed if l.code == to_code), None)
        if from_lang is None or to_lang is None:
            return False
        translation = from_lang.get_translation(to_lang)
        # 排除 IdentityTranslation（同语言占位，无实际翻译能力）
        return translation is not None and translation.from_lang.code != translation.to_lang.code

    def translate(self, text: str, from_code: str, to_code: str) -> str:
        if not text.strip():
            return ""
        if not self._ready:
            self.setup()
        result = argostranslate.translate.translate(text, from_code, to_code)
        logger.debug("翻译 [%s->%s]: %s", from_code, to_code, result)
        return result.strip()
