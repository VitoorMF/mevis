"""Orquestração do pipeline horário (doc 03 §6.3).

Etapas, todas idempotentes (reexecutar não duplica nada):

1. fechar arquivos `.open` órfãos (sessão encerrada ou hora vencida há mais que a carência);
2. comprimir e verificar os arquivos fechados (RF-137) e registrá-los no estado;
3. normalizar as horas cujas entradas mudaram desde a última execução (RF-133) e gerar a
   máscara de qualidade (RF-159);
4. exportar os datasets operacionais (doc 06 §5.4) e uma cópia do estado;
5. replicar no armazenamento durável o que ainda não foi replicado (RF-136);
6. apagar do disco local o que já foi replicado e passou da retenção (RF-138).

Uma hora só é normalizada quando todos os seus arquivos brutos estão fechados. Se arquivos
novos aparecerem depois (vizinhos, recuperações, lacunas detectadas mais tarde), o resumo das
entradas muda e a hora é normalizada de novo.
"""

import fcntl
import hashlib
import logging
import os
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from trader.core.timeunits import SECOND_US
from trader.ops.config import PipelineConfig
from trader.ops.logs import ctx
from trader.ops.state import RawFileRow, StateStore
from trader.pipeline.normalize_hour import normalize_hour
from trader.quality.mask import ClockReference, GapSpan, build_mask
from trader.storage import parquet
from trader.storage.layout import (
    HOUR_US,
    RawFile,
    RawState,
    dataset_path,
    relative,
    scan_raw,
)
from trader.storage.objectstore import ObjectStore
from trader.storage.rawfiles import compress_file, content_digest, file_sha256

log = logging.getLogger(__name__)
OPS_TABLES = ("sessions", "gaps", "clock_offsets")


class PipelineBusy(RuntimeError):
    """Outra execução do pipeline está em andamento (doc 03 §6.3)."""


@dataclass
class PipelineReport:
    sealed_orphans: int = 0
    compressed: int = 0
    normalized_hours: list[int] = field(default_factory=list)
    rows: Counter[str] = field(default_factory=Counter)
    duplicates: Counter[str] = field(default_factory=Counter)
    quarantined: int = 0
    replicated: int = 0
    deleted: int = 0


class HourlyPipeline:
    def __init__(
        self, config: PipelineConfig, state: StateStore, store: ObjectStore | None
    ) -> None:
        self._config = config
        self._state = state
        self._store = store
        self._data_dir = config.data_dir

    def run(self, now_us: int, *, rebuild: bool = False) -> PipelineReport:
        report = PipelineReport()
        with self._lock():
            self._seal_orphans(now_us, report)
            self._compress(now_us, report)
            self._normalize(now_us, report, rebuild=rebuild)
            self._export_ops(now_us)
            self._replicate(now_us, report)
            self._apply_retention(now_us, report)
        log.info(
            "pipeline concluído",
            extra=ctx(
                compressed=report.compressed,
                normalized_hours=len(report.normalized_hours),
                quarantined=report.quarantined,
                replicated=report.replicated,
                deleted=report.deleted,
            ),
        )
        return report

    # ------------------------------------------------------------------ etapas

    def _seal_orphans(self, now_us: int, report: PipelineReport) -> None:
        closed = self._state.closed_sessions()
        grace_us = int(self._config.orphan_grace_s * SECOND_US)
        for raw in self._scan(RawState.OPEN):
            hour_over = (raw.hour + 1) * HOUR_US + grace_us <= now_us
            if raw.session in closed or hour_over:
                target = raw.path.with_name(raw.path.name.removesuffix(".open"))
                os.rename(raw.path, target)
                report.sealed_orphans += 1
                log.warning(
                    "arquivo órfão fechado", extra=ctx(path=relative(self._data_dir, target))
                )

    def _compress(self, now_us: int, report: PipelineReport) -> None:
        for raw in self._scan(RawState.SEALED):
            result = compress_file(raw.path, self._config.compression_level)
            self._register_raw(raw, result.path, result.sha256, result.original_size, now_us)
            report.compressed += 1
        # Recuperação após queda entre a compressão e o registro.
        for raw in self._scan(RawState.COMPRESSED):
            if self._state.raw_file(relative(self._data_dir, raw.path)) is None:
                sha256, size = content_digest(raw.path)
                self._register_raw(raw, raw.path, sha256, size, now_us)

    def _register_raw(
        self, raw: RawFile, path: Path, sha256: str, original_size: int, now_us: int
    ) -> None:
        self._state.register_raw_file(
            RawFileRow(
                rel_path=relative(self._data_dir, path),
                channel_dir=raw.channel_dir,
                session_id=raw.session,
                hour=raw.hour,
                sha256=sha256,
                file_sha256=file_sha256(path),
                original_size=original_size,
                compressed_size=path.stat().st_size,
                compressed_ts_us=now_us,
            )
        )

    def _normalize(self, now_us: int, report: PipelineReport, *, rebuild: bool) -> None:
        streams = set(self._config.streams)
        rows = [r for r in self._state.raw_files() if r.channel_dir in streams]
        open_hours = {f.hour for f in self._scan(RawState.OPEN) if f.channel_dir in streams}
        for hour in sorted({r.hour for r in rows}):
            if hour in open_hours:
                continue
            inputs = [r for r in rows if hour - 1 <= r.hour <= hour + 1]
            if any(r.deleted_ts_us is not None for r in inputs):
                continue  # entradas já removidas pela retenção: a hora não é mais refeita aqui
            gaps = self._gaps_near(hour)
            clock = self._clock_near(hour)
            digest = self._digest(inputs, gaps, clock)
            if not rebuild and self._state.normalized_digest(self._config.symbol, hour) == digest:
                continue
            files = [self._raw_file(r) for r in inputs]
            reference = ClockReference(
                clock, int(self._config.clock_reference_window_s * SECOND_US)
            )
            result = normalize_hour(self._config, hour, files, reference)
            mask = build_mask(
                hour_start_us=hour * HOUR_US,
                channels=self._config.streams,
                flags=result.flags,
                gaps=gaps,
                clock=reference,
                offset_alert_us=int(self._config.clock_offset_alert_ms * 1000),
            )
            mask_path = dataset_path(
                self._data_dir, "derived", "quality_mask", self._config.symbol, hour
            )
            parquet.write_rows(
                mask_path,
                parquet.QUALITY_MASK,
                [
                    {
                        "symbol": self._config.symbol,
                        "channel": row.channel,
                        "minute_ts_us": row.minute_ts_us,
                        "status": row.status.value,
                        "details": row.details,
                        "schema_version": 1,
                    }
                    for row in mask
                ],
            )
            for path in [*result.outputs, mask_path]:
                self._state.register_output(
                    relative(self._data_dir, path), hour, file_sha256(path), now_us
                )
            for path in result.removed:
                self._state.mark_output(relative(self._data_dir, path), deleted_ts_us=now_us)
            self._state.set_normalized(self._config.symbol, hour, digest, now_us)
            report.normalized_hours.append(hour)
            report.rows.update(result.rows)
            report.duplicates.update(result.duplicates)
            report.quarantined += sum(result.quarantined.values())

    def _export_ops(self, now_us: int) -> None:
        directory = self._data_dir / "derived" / "ops"
        for table in OPS_TABLES:
            columns, rows = self._state.table_rows(table)
            path = directory / f"{table}.parquet"
            directory.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            data = pa.Table.from_pylist([dict(zip(columns, row, strict=True)) for row in rows])
            if not rows:
                data = pa.table({c: pa.array([], pa.null()) for c in columns})
            pq.write_table(data, tmp, compression="zstd")
            tmp.replace(path)
            self._state.register_output(
                relative(self._data_dir, path), None, file_sha256(path), now_us
            )
        backup = self._data_dir / "ops" / "state-backup.db"
        backup.unlink(missing_ok=True)
        self._state.backup_to(backup)
        self._state.register_output(
            relative(self._data_dir, backup), None, file_sha256(backup), now_us
        )

    def _replicate(self, now_us: int, report: PipelineReport) -> None:
        if self._store is None:
            return
        for raw in self._state.raw_files("replicated_ts_us IS NULL AND deleted_ts_us IS NULL"):
            self._store.put(self._data_dir / raw.rel_path, raw.rel_path, raw.file_sha256)
            self._state.mark_raw(raw.rel_path, replicated_ts_us=now_us)
            report.replicated += 1
        for output in self._state.outputs("replicated_ts_us IS NULL AND deleted_ts_us IS NULL"):
            path = self._data_dir / output.rel_path
            if file_sha256(path) != output.file_sha256:
                continue  # mudou após o registro; entra na próxima execução
            self._store.put(path, output.rel_path, output.file_sha256)
            self._state.mark_output(output.rel_path, replicated_ts_us=now_us)
            report.replicated += 1

    def _apply_retention(self, now_us: int, report: PipelineReport) -> None:
        cutoff_hour = (
            now_us - int(self._config.local_retention_days * 86_400 * SECOND_US)
        ) // HOUR_US
        replicated_and_old = "replicated_ts_us IS NOT NULL AND deleted_ts_us IS NULL AND hour < ?"
        for raw in self._state.raw_files(replicated_and_old, (cutoff_hour,)):
            (self._data_dir / raw.rel_path).unlink(missing_ok=True)
            self._state.mark_raw(raw.rel_path, deleted_ts_us=now_us)
            report.deleted += 1
        for output in self._state.outputs(replicated_and_old, (cutoff_hour,)):
            (self._data_dir / output.rel_path).unlink(missing_ok=True)
            self._state.mark_output(output.rel_path, deleted_ts_us=now_us)
            report.deleted += 1

    # ------------------------------------------------------------------ apoio

    def _scan(self, state: RawState) -> list[RawFile]:
        return [f for f in scan_raw(self._data_dir, self._config.symbol) if f.state is state]

    def _raw_file(self, row: RawFileRow) -> RawFile:
        return RawFile(
            self._data_dir / row.rel_path,
            row.channel_dir,
            row.hour,
            row.session_id,
            RawState.COMPRESSED,
        )

    def _gaps_near(self, hour: int) -> list[GapSpan]:
        start, end = hour * HOUR_US, (hour + 1) * HOUR_US
        return [
            GapSpan(g.record.channel, g.record.start_ts_us, g.record.end_ts_us, g.status)
            for g in self._state.gaps_overlapping(start, end)
        ]

    def _clock_near(self, hour: int) -> list[tuple[int, int]]:
        window = int(self._config.clock_reference_window_s * SECOND_US)
        return self._state.clock_offsets_between(
            hour * HOUR_US - window, (hour + 1) * HOUR_US + window
        )

    @staticmethod
    def _digest(inputs: list[RawFileRow], gaps: list[GapSpan], clock: list[tuple[int, int]]) -> str:
        digest = hashlib.sha256()
        for row in sorted(inputs, key=lambda r: r.rel_path):
            digest.update(f"raw|{row.rel_path}|{row.sha256}\n".encode())
        for gap in sorted(gaps, key=lambda g: (g.channel, g.start_ts_us, g.end_ts_us)):
            digest.update(
                f"gap|{gap.channel}|{gap.start_ts_us}|{gap.end_ts_us}|{gap.status}\n".encode()
            )
        for local_ts, offset in clock:
            digest.update(f"clock|{local_ts}|{offset}\n".encode())
        return digest.hexdigest()

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        with (self._data_dir / "pipeline.lock").open("w") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise PipelineBusy("pipeline já em execução") from exc
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
