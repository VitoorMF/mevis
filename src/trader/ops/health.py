"""Arquivo de saúde do coletor (doc 03 §4.2): JSON reescrito atomicamente a cada poucos segundos.

Cada instância do coletor tem o seu (`collector-<instância>.json`), para que duas versões possam
rodar juntas durante a troca sem lacuna (doc 03 §9.2).
"""

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def health_path(data_dir: Path, instance: str = "main") -> Path:
    return data_dir / "health" / f"collector-{instance}.json"


def health_files(data_dir: Path) -> list[Path]:
    return sorted((data_dir / "health").glob("collector-*.json"))


def write_health(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
    os.replace(tmp, path)


def is_ready(health: Mapping[str, Any] | None, since_us: int, now_us: int, max_age_us: int) -> bool:
    """A instância iniciada depois de `since_us` está recebendo todos os canais (RF-186).

    O arquivo sobrevive ao processo: sem conferir início e idade, o de uma execução anterior
    passaria por pronto (doc 03 §9.2)."""
    if health is None or health.get("status") != "running":
        return False
    started, updated = health.get("started_ts_us"), health.get("updated_ts_us")
    if not isinstance(started, int) or not isinstance(updated, int):
        return False
    if started < since_us or now_us - updated > max_age_us:
        return False
    channels = health.get("channels")
    if not isinstance(channels, dict) or not channels:
        return False
    return all(c.get("messages", 0) > 0 for c in channels.values())


def read_health(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
