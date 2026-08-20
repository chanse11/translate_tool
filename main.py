"""实时翻译工具入口。"""

from __future__ import annotations

import logging
import sys
from pathlib import Path


def setup_logging() -> None:
    from config_loader import app_dir

    log_dir = app_dir() / "logs"
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

    from config_loader import ensure_user_config, load_config

    if len(sys.argv) > 1:
        config_path = Path(sys.argv[1])
    else:
        config_path = ensure_user_config()

    config = load_config(config_path)
    logging.info("配置已加载: %s", config_path)

    # 先启动 Qt，再加载含 sounddevice / whisper 等重依赖的界面模块
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    logging.info("Qt 已初始化，正在加载界面模块…")

    from ui.main_window import MainWindow

    logging.info("正在创建主窗口…")
    window = MainWindow(config, config_path=config_path)
    window.showMaximized()
    window.raise_()
    window.activateWindow()
    logging.info("界面已显示，进入事件循环")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
