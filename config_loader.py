"""配置加载。"""

from __future__ import annotations

from pathlib import Path

import yaml


def load_config(path: str | Path | None = None) -> dict:
    if path is None:
        path = Path(__file__).resolve().parent.parent / "config.yaml"
    path = Path(path)
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
