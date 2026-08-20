"""配置加载与保存（兼容开发环境与 PyInstaller 冻结包）。"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import yaml


def app_dir() -> Path:
    """可写的应用根目录：exe 同级（冻结）或项目根目录（开发）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_dir() -> Path:
    """只读资源目录：PyInstaller 的 _MEIPASS，或开发时的项目根。"""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def ensure_user_config(config_name: str = "config.yaml") -> Path:
    """
    确保 exe/项目同级有可编辑的 config.yaml。
    若不存在，则从打包内置资源复制一份。
    """
    user_path = app_dir() / config_name
    if user_path.exists():
        return user_path

    bundled = resource_dir() / config_name
    if bundled.exists():
        try:
            shutil.copy2(bundled, user_path)
        except OSError:
            # 复制失败时仍返回用户路径，load 时可回退读 bundled
            pass
    return user_path


def load_config(path: str | Path | None = None) -> dict:
    if path is None:
        path = ensure_user_config()
    path = Path(path)
    if not path.exists():
        bundled = resource_dir() / path.name
        if bundled.exists():
            path = bundled
        else:
            return {}
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_config(config: dict, path: str | Path | None = None) -> None:
    if path is None:
        path = app_dir() / "config.yaml"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)
