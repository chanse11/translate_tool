"""Zoom 实时翻译工具入口。"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from config_loader import load_config
from ui.main_window import run_app


def setup_logging() -> None:
    log_dir = Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_dir / "translate_tool.log", encoding="utf-8"),
        ],
    )


def main() -> None:
    setup_logging()
    config_path = Path(__file__).resolve().parent / "config.yaml"
    if len(sys.argv) > 1:
        config_path = Path(sys.argv[1])
    config = load_config(config_path)
    logging.info("配置已加载: %s", config_path)
    run_app(config)


if __name__ == "__main__":
    main()
