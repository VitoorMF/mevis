"""Layout de caminhos das camadas de dados (doc 06 §5) e utilidades de disco."""

import os
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path

HOUR_US = 3_600_000_000
EXCHANGE = "binance-spot"
OPEN_SUFFIX = ".open"
SEALED_SUFFIX = ".jsonl"
COMPRESSED_SUFFIX = ".jsonl.zst"

RECOVERED_KLINES = "rest:klines"
RECOVERED_TRADES = "archive:trades"
RECOVERY_CHANNELS = (RECOVERED_KLINES, RECOVERED_TRADES)
"""Canais brutos escritos pela recuperação de lacunas (doc 06 §9.1)."""

_RAW_NAME = re.compile(r"^(?P<session>[A-Za-z0-9._-]+)\.jsonl(?P<suffix>\.open|\.zst)?$")
_DATE_DIR = re.compile(r"^date=(\d{4}-\d{2}-\d{2})$")
_HOUR_DIR = re.compile(r"^hour=(\d{2})$")


class StorageError(Exception):
    """Falha de disco ao gravar dados. É fatal para quem grava (RNF-004)."""


class RawState(Enum):
    OPEN = "open"
    SEALED = "sealed"
    COMPRESSED = "compressed"


@dataclass(frozen=True, slots=True)
class RawFile:
    path: Path
    channel_dir: str
    hour: int
    session: str
    state: RawState


def channel_dir(channel: str) -> str:
    """`rest:depth` → `rest_depth`; o nome original continua no envelope."""
    return channel.replace(":", "_")


def raw_root(data_dir: Path, symbol: str) -> Path:
    return data_dir / "raw" / EXCHANGE / symbol.lower()


def hour_dirs(hour: int) -> tuple[str, str]:
    """Índice de hora (µs // HOUR_US) → (`date=AAAA-MM-DD`, `hour=HH`)."""
    start = datetime.fromtimestamp(hour * HOUR_US / 1e6, UTC)
    return f"date={start:%Y-%m-%d}", f"hour={start:%H}"


def hour_from_dirs(date_dir: str, hour_dir: str) -> int | None:
    date_match, hour_match = _DATE_DIR.match(date_dir), _HOUR_DIR.match(hour_dir)
    if date_match is None or hour_match is None:
        return None
    day = datetime.strptime(date_match.group(1), "%Y-%m-%d").replace(tzinfo=UTC)
    return int(day.timestamp()) // 3600 + int(hour_match.group(1))


def scan_raw(data_dir: Path, symbol: str) -> list[RawFile]:
    """Todos os arquivos brutos do ativo, em ordem determinística de caminho."""
    files = []
    for path in sorted(raw_root(data_dir, symbol).glob("*/date=*/hour=*/*")):
        match = _RAW_NAME.match(path.name)
        hour = hour_from_dirs(path.parent.parent.name, path.parent.name)
        if match is None or hour is None:
            continue
        state = {
            ".open": RawState.OPEN,
            ".zst": RawState.COMPRESSED,
            None: RawState.SEALED,
        }[match.group("suffix")]
        files.append(
            RawFile(path, path.parent.parent.parent.name, hour, match.group("session"), state)
        )
    return files


def dataset_path(data_dir: Path, layer: str, dataset: str, symbol: str, hour: int) -> Path:
    """Arquivo Parquet de uma hora: `<camada>/<dataset>/symbol=X/date=AAAA-MM-DD/HH.parquet`."""
    date_dir, hour_dir = hour_dirs(hour)
    name = hour_dir.removeprefix("hour=") + ".parquet"
    return data_dir / layer / dataset / f"symbol={symbol}" / date_dir / name


def quarantine_path(data_dir: Path, symbol: str, hour: int) -> Path:
    """Registros recusados na normalização de uma hora; cada linha informa canal e motivo."""
    date_dir, hour_dir = hour_dirs(hour)
    return (
        data_dir / "quarantine" / EXCHANGE / symbol.lower() / date_dir / hour_dir
    ) / "normalize.jsonl.zst"


def archive_dataset_path(data_dir: Path, dataset: str, symbol: str, day: date) -> Path:
    """Dia importado pelo backfill: `normalized/<dataset>/symbol=X/date=…/archive.parquet`."""
    return (
        data_dir
        / "normalized"
        / dataset
        / f"symbol={symbol}"
        / f"date={day:%Y-%m-%d}"
        / ("archive.parquet")
    )


def archive_quarantine_path(data_dir: Path, symbol: str, dataset: str, day: date) -> Path:
    """Registros recusados no backfill de um dia (doc 06 §7)."""
    return (
        data_dir / "quarantine" / EXCHANGE / symbol.lower() / f"date={day:%Y-%m-%d}"
    ) / f"archive-{dataset}.jsonl.zst"


def relative(data_dir: Path, path: Path) -> str:
    """Caminho relativo ao diretório de dados; também é a chave no armazenamento durável."""
    return path.relative_to(data_dir).as_posix()


def fsync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def replace_atomically(tmp: Path, final: Path) -> None:
    os.replace(tmp, final)
    fsync_dir(final.parent)
