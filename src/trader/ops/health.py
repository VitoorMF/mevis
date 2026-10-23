"""Arquivo de saúde do coletor (doc 03 §4.2): JSON reescrito atomicamente a cada poucos segundos."""

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def health_path(data_dir: Path) -> Path:
    return data_dir / "health" / "collector.json"


def write_health(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
    os.replace(tmp, path)


def read_health(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
