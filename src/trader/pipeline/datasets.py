"""Construção dos datasets da V0.2 (doc 03 §5.2; RF-226 a RF-229).

Monta a série de minutos com o preço de execução, calcula as features e o alvo e grava uma tabela
por trilha e período: o Parquet local em `<out_dir>/<id>.parquet`, com o manifesto ao lado, e a
cópia em `derived/datasets/<id>/` (`data.parquet` e `manifest.json`). O id resume tudo o que
determina o conteúdo (RF-227): construir de novo com as mesmas entradas e a mesma versão do
código gera o mesmo arquivo, byte a byte (CA-07); por isso o Parquet não guarda a hora da
construção, que fica só no manifesto.

A trava do holdout (RF-228) vem antes de qualquer leitura. Com a opção da avaliação final, o
registro de acesso vai para `derived/ops/holdout_access/` antes de ler os dados, e um segundo
registro, com o id do dataset, depois da construção. Usa `numpy`: só a CLI o importa, dentro do
comando (RNF-029).
"""

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from trader.core.holdout import refuse_holdout, refuse_holdout_b
from trader.datasets.labels import (
    TRACK_FAMILIES,
    LabelParams,
    compute_labels,
    dataset_table,
)
from trader.features.compute import catalog_id, compute_features
from trader.features.series import series_from_table
from trader.pipeline.minutes import Source, assemble_minutes
from trader.storage.objectstore import ObjectStore
from trader.storage.rawfiles import file_sha256

DATASETS = "derived/datasets"
ACCESS_LOG = "derived/ops/holdout_access"
METADATA_KEY = b"mevis.dataset"
_REPO = Path(__file__).resolve().parents[3]
_CODE_PATHS = ("src", "pyproject.toml", "uv.lock")


@dataclass(frozen=True)
class DatasetRequest:
    track: str
    start: date
    end: date
    final_evaluation: bool = False
    params: LabelParams = field(default_factory=LabelParams)


@dataclass
class Built:
    dataset_id: str
    path: Path
    manifest: dict[str, Any]
    minutes: int
    eligible: dict[int, int]
    positives: dict[int, int] | None
    """Alvos positivos entre os pontos elegíveis; `None` na avaliação final (RF-228)."""
    warnings: list[str]


def code_version(repo: Path = _REPO) -> str:
    """Versão do pacote com o commit; `.dirty` se o código tem mudanças sem commit."""
    base = version("trader")
    try:
        commit = _git(repo, "rev-parse", "--short=12", "HEAD")
        dirty = _git(repo, "status", "--porcelain", "--", *_CODE_PATHS)
    except (OSError, subprocess.CalledProcessError):
        return base
    return f"{base}+g{commit}" + (".dirty" if dirty else "")


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def dataset_id(identity: dict[str, Any]) -> str:
    """Resumo SHA-256 (16 primeiros dígitos) da identificação canônica (RF-227)."""
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def build_dataset(
    source: Source,
    *,
    symbol: str,
    kline_channel: str,
    request: DatasetRequest,
    code: str,
    out_dir: Path,
    store: ObjectStore,
    now: datetime,
    who: str,
) -> Built:
    if request.track not in TRACK_FAMILIES:
        raise ValueError(f"trilha desconhecida: {request.track}")
    final = request.final_evaluation
    reserved = refuse_holdout(request.start, request.end, allow=final, requirement="RF-228")
    touches_b = refuse_holdout_b(request.start, request.end, allow=final, requirement="RF-228")
    stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    access = {
        "who": who,
        "at": now.astimezone(UTC).isoformat(),
        "track": request.track,
        "start": request.start.isoformat(),
        "end": request.end.isoformat(),
        "code_version": code,
        "holdout_a_days": len(reserved),
        "holdout_b": touches_b,
    }
    if final:  # antes de ler qualquer arquivo (RF-228)
        _put_json(store, out_dir, f"{ACCESS_LOG}/{stamp}.json", access | {"stage": "request"})

    minutes = assemble_minutes(
        source,
        symbol=symbol,
        kline_channel=kline_channel,
        start=request.start,
        end=request.end,
        allow_holdout=final,
        execution=True,
    )
    assert minutes.execution is not None
    price_column = minutes.execution[request.params.price_column]
    price = pc.cast(price_column, pa.float64()).to_numpy(zero_copy_only=False)
    series = series_from_table(minutes.table)
    del minutes.table, minutes.execution
    labels = compute_labels(price, series.trades.valid, request.params)
    frame = compute_features(series)
    del series
    table = dataset_table(frame, labels, request.track)
    del frame

    inputs = sorted(minutes.inputs, key=lambda i: i["key"])
    identity = {
        "track": request.track,
        "start": request.start.isoformat(),
        "end": request.end.isoformat(),
        "final_evaluation": final,
        "catalog_id": catalog_id(),
        "code_version": code,
        "label": request.params.to_dict(),
        "symbol": symbol,
        "inputs": inputs,
    }
    ident = dataset_id(identity)
    inputs_sha256 = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    summary = {k: v for k, v in identity.items() if k != "inputs"}
    summary |= {"dataset_id": ident, "inputs": len(inputs), "inputs_sha256": inputs_sha256}
    table = table.replace_schema_metadata({METADATA_KEY: json.dumps(summary, sort_keys=True)})

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{ident}.parquet"
    tmp = path.with_name(path.name + ".tmp")
    pq.write_table(table, tmp, compression="zstd")
    os.replace(tmp, path)
    parquet_sha256 = file_sha256(path)

    eligible = {h: int(np.sum(table[f"eligible_h{h}"])) for h in request.params.horizons}
    manifest = {
        "dataset_id": ident,
        "identity": identity,
        "parquet_sha256": parquet_sha256,
        "rows": table.num_rows,
        "columns": table.column_names,
        "eligible": {str(h): n for h, n in eligible.items()},
        "built_at": now.astimezone(UTC).isoformat(),
        "built_by": who,
        "final_evaluation": final,
        "holdout_a_days": len(reserved),
        "holdout_b": touches_b,
        "holdout_access": f"{ACCESS_LOG}/{stamp}.json" if final else None,
        "archive_days": minutes.archive_days,
        "live_hours": minutes.live_hours,
        "warnings": minutes.warnings,
    }
    manifest_path = out_dir / f"{ident}.manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    store.put(path, f"{DATASETS}/{ident}/data.parquet", parquet_sha256)
    store.put(manifest_path, f"{DATASETS}/{ident}/manifest.json", file_sha256(manifest_path))
    if final:
        result = access | {
            "stage": "result",
            "dataset_id": ident,
            "parquet_sha256": parquet_sha256,
        }
        _put_json(store, out_dir, f"{ACCESS_LOG}/{stamp}.result.json", result)

    positives = None
    if not final:
        positives = {
            h: int(np.sum(pc.and_(table[f"eligible_h{h}"], table[f"target_h{h}"]).fill_null(False)))
            for h in request.params.horizons
        }
    return Built(ident, path, manifest, table.num_rows, eligible, positives, minutes.warnings)


def _put_json(store: ObjectStore, out_dir: Path, key: str, record: dict[str, Any]) -> None:
    """Grava um registro novo (a hora no nome não se repete): só acréscimo, nunca substitui."""
    out_dir.mkdir(parents=True, exist_ok=True)
    local = out_dir / ".access" / key.rsplit("/", 1)[1]
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    store.put(local, key, file_sha256(local))
