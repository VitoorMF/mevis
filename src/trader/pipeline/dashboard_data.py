"""Dados do painel da coleta ao vivo (doc 06 §8.4): sincroniza do S3 e agrega em CSVs pequenos.

Só lê a coleta ao vivo: arquivos horários desde o início da coleta. O backfill (`archive.parquet`)
e o período anterior ficam de fora, o que mantém o holdout da trilha A sem consulta (doc 04 §3.3).
"""

import csv
import json
import os
import re
import sqlite3
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

MINUTE_US = 60_000_000
HOUR_US = 3_600_000_000
SYNCED = (
    "normalized/trades",
    "normalized/klines_1m",
    "normalized/book_top20",
    "derived/quality_mask",
)
STATE_BACKUP = "ops/state-backup.db"
CHANNEL_NAMES = {
    "trade": "Negócios",
    "kline_1m": "Candles",
    "depth20@100ms": "Livro top 20",
    "depth@100ms": "Livro diferencial",
}
_HOURLY = re.compile(r"/date=\d{4}-\d{2}-\d{2}/\d{2}\.parquet$")


@dataclass
class SyncReport:
    downloaded: int = 0
    unchanged: int = 0


@dataclass
class ExportReport:
    collection_start: str
    rows: dict[str, int] = field(default_factory=dict)


# ------------------------------------------------------------------ sincronização


def sync_from_s3(
    client: Any, bucket: str, data_dir: Path, symbol: str, today: date | None = None
) -> SyncReport:
    """Baixa para `data_dir` o que mudou no S3 desde a última vez (pelo ETag de cada objeto)."""
    manifest_path = data_dir / ".s3-etags.json"
    try:
        manifest: dict[str, str] = json.loads(manifest_path.read_text())
    except (OSError, ValueError):
        manifest = {}
    report = SyncReport()
    _download(client, bucket, data_dir, STATE_BACKUP, None, manifest, report)
    start = _collection_start(data_dir / STATE_BACKUP)
    if start is not None:
        last = today or datetime.now(UTC).date()
        first = datetime.fromtimestamp(start / 1e6, UTC).date()
        for dataset in SYNCED:
            for day in _days(first, last):
                prefix = f"{dataset}/symbol={symbol}/date={day:%Y-%m-%d}/"
                for obj in _list(client, bucket, prefix):
                    if _HOURLY.search(obj["Key"]):
                        _download(client, bucket, data_dir, obj["Key"], obj, manifest, report)
    data_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=1, sort_keys=True))
    return report


def _list(client: Any, bucket: str, prefix: str) -> Iterator[dict[str, Any]]:
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        yield from page.get("Contents", [])


def _download(
    client: Any,
    bucket: str,
    data_dir: Path,
    key: str,
    listed: dict[str, Any] | None,
    manifest: dict[str, str],
    report: SyncReport,
) -> None:
    if listed is None:
        listed = {"ETag": client.head_object(Bucket=bucket, Key=key)["ETag"]}
    target = data_dir / key
    if manifest.get(key) == listed["ETag"] and target.exists():
        report.unchanged += 1
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    client.download_file(bucket, key, str(tmp))
    os.replace(tmp, target)
    manifest[key] = listed["ETag"]
    report.downloaded += 1


# ------------------------------------------------------------------ agregação


def export(data_dir: Path, out_dir: Path, symbol: str) -> ExportReport:
    """Grava os cinco CSVs de doc 06 §8.4 em `out_dir` a partir de `data_dir`."""
    state_path = data_dir / "state.db"
    if not state_path.exists():
        state_path = data_dir / STATE_BACKUP
    start = _collection_start(state_path)
    if start is None:
        raise ValueError(f"nenhuma sessão de coleta em {state_path}")
    first_minute = start // MINUTE_US * MINUTE_US + MINUTE_US  # primeiro minuto completo
    out_dir.mkdir(parents=True, exist_ok=True)
    report = ExportReport(collection_start=_iso(start))

    minutes: dict[int, dict[str, float]] = defaultdict(dict)
    delays: dict[int, tuple[int, float, float]] = {}
    _klines(data_dir, symbol, first_minute, minutes)
    _trades(data_dir, symbol, first_minute, minutes, delays)
    last_book = _book(data_dir, symbol, first_minute, minutes)

    columns = [
        "minute", "close", "volume", "buy_volume", "sell_volume", "trades", "snapshots",
        "spread_bps", "imbalance_top20", "imbalance_level1",
    ]  # fmt: skip
    rows = []
    for minute in sorted(minutes):
        d = minutes[minute]
        if "close" not in d:
            continue  # minuto ainda sem candle fechado
        rows.append([
            _iso(minute), d["close"], round(d["volume"], 5), round(d.get("buy", 0.0), 5),
            round(d.get("sell", 0.0), 5), int(d.get("trades", 0)), int(d.get("snapshots", 0)),
            _round(d.get("spread_bps"), 4), _round(d.get("imb20"), 4), _round(d.get("imb1"), 4),
        ])  # fmt: skip
    report.rows["minutes.csv"] = _write(out_dir / "minutes.csv", columns, rows)

    book_rows = []
    if last_book is not None:
        for side, px, qty in (("compra", "bid_px", "bid_qty"), ("venda", "ask_px", "ask_qty")):
            for level, (price, quantity) in enumerate(
                zip(last_book[px], last_book[qty], strict=True), 1
            ):
                book_rows.append(
                    [_iso(last_book["recv_ts_us"]), side, level, float(price), float(quantity)]
                )
    report.rows["book_snapshot.csv"] = _write(
        out_dir / "book_snapshot.csv",
        ["captured_at", "side", "level", "price", "quantity"],
        book_rows,
    )

    report.rows["quality_hourly.csv"] = _write(
        out_dir / "quality_hourly.csv",
        ["hour", "channel", "ok", "recovered", "missing", "other"],
        _quality(data_dir, symbol, first_minute),
    )
    report.rows["delays_hourly.csv"] = _write(
        out_dir / "delays_hourly.csv",
        ["hour", "live_trades", "delay_p50_ms", "delay_p99_ms"],
        [[_iso(hour), *delays[hour]] for hour in sorted(delays)],
    )
    report.rows["gaps.csv"] = _write(
        out_dir / "gaps.csv",
        ["gap_id", "channel", "cause", "status", "started_at", "duration_s", "recovery_origin"],
        _gaps(state_path),
    )
    return report


def _klines(data_dir: Path, symbol: str, first: int, minutes: dict[int, dict[str, float]]) -> None:
    for path in _hourly(data_dir, "normalized/klines_1m", symbol):
        table = pq.read_table(path, columns=["open_ts_us", "close", "volume"])
        for open_ts, close, volume in zip(
            *(table[c].to_pylist() for c in table.column_names), strict=True
        ):
            if open_ts >= first:
                minutes[open_ts].update(close=float(close), volume=float(volume))


def _trades(
    data_dir: Path,
    symbol: str,
    first: int,
    minutes: dict[int, dict[str, float]],
    delays: dict[int, tuple[int, float, float]],
) -> None:
    for path in _hourly(data_dir, "normalized/trades", symbol):
        table = pq.read_table(path, columns=["trade_ts_us", "qty", "aggressor", "recv_ts_us"])
        table = table.filter(pc.greater_equal(table["trade_ts_us"], first))
        if table.num_rows == 0:
            continue
        minute = pc.multiply(pc.divide(table["trade_ts_us"], MINUTE_US), MINUTE_US)
        grouped = (
            pa.table(
                {
                    "minute": minute,
                    "qty": pc.cast(table["qty"], pa.float64()),
                    "side": table["aggressor"],
                }
            )
            .group_by(["minute", "side"])
            .aggregate([("qty", "sum"), ("qty", "count")])
        )
        for m, side, qty, count in zip(
            *(grouped[c].to_pylist() for c in ("minute", "side", "qty_sum", "qty_count")),
            strict=True,
        ):
            d = minutes[m]
            name = "buy" if side == "B" else "sell"
            d[name] = d.get(name, 0.0) + qty
            d["trades"] = d.get("trades", 0) + count
        live = table.filter(pc.is_valid(table["recv_ts_us"]))
        if live.num_rows:
            delay_ms = pc.divide(
                pc.cast(pc.subtract(live["recv_ts_us"], live["trade_ts_us"]), pa.float64()), 1000.0
            )
            p50, p99 = pc.quantile(delay_ms, q=[0.5, 0.99]).to_pylist()
            hour = int(_hour_of(path))
            delays[hour] = (live.num_rows, round(p50, 1), round(p99, 1))


def _book(
    data_dir: Path, symbol: str, first: int, minutes: dict[int, dict[str, float]]
) -> dict[str, Any] | None:
    last = None
    for path in _hourly(data_dir, "normalized/book_top20", symbol):
        table = pq.read_table(
            path, columns=["recv_ts_us", "bid_px", "bid_qty", "ask_px", "ask_qty"]
        )
        table = table.filter(pc.greater_equal(table["recv_ts_us"], first))
        if table.num_rows == 0:
            continue
        best = {
            c: pc.cast(pc.list_element(table[c], 0), pa.float64()) for c in table.column_names[1:]
        }
        bid_total, ask_total = _list_sum(table["bid_qty"]), _list_sum(table["ask_qty"])
        mid = pc.divide(pc.add(best["bid_px"], best["ask_px"]), 2.0)
        spread = pc.subtract(best["ask_px"], best["bid_px"])
        stats = pa.table(
            {
                "minute": pc.multiply(pc.divide(table["recv_ts_us"], MINUTE_US), MINUTE_US),
                "spread_bps": pc.multiply(pc.divide(spread, mid), 10_000.0),
                "imb20": pc.divide(bid_total, pc.add(bid_total, ask_total)),
                "imb1": pc.divide(best["bid_qty"], pc.add(best["bid_qty"], best["ask_qty"])),
            }
        )
        aggregations = [("spread_bps", "mean"), ("imb20", "mean"), ("imb1", "mean")]
        grouped = stats.group_by("minute").aggregate([*aggregations, ("imb20", "count")])
        columns = ("minute", "spread_bps_mean", "imb20_mean", "imb1_mean", "imb20_count")
        for m, spread, imb20, imb1, count in zip(
            *(grouped[c].to_pylist() for c in columns), strict=True
        ):
            minutes[m].update(spread_bps=spread, imb20=imb20, imb1=imb1, snapshots=count)
        candidate = table.slice(table.num_rows - 1, 1).to_pylist()[0]
        if last is None or candidate["recv_ts_us"] > last["recv_ts_us"]:
            last = candidate
    return last


def _quality(data_dir: Path, symbol: str, first: int) -> list[list[Any]]:
    counts: dict[tuple[int, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for path in _hourly(data_dir, "derived/quality_mask", symbol):
        table = pq.read_table(path, columns=["channel", "minute_ts_us", "status"])
        for channel, minute, status in zip(
            *(table[c].to_pylist() for c in table.column_names), strict=True
        ):
            if minute < first:
                continue
            key = status if status in ("ok", "recovered", "missing") else "other"
            name = CHANNEL_NAMES.get(channel.partition("@")[2], channel)
            counts[(minute // HOUR_US * HOUR_US, name)][key] += 1
    return [
        [_iso(hour), name, c["ok"], c["recovered"], c["missing"], c["other"]]
        for (hour, name), c in sorted(counts.items())
    ]


def _gaps(state_path: Path) -> list[list[Any]]:
    db = sqlite3.connect(f"file:{state_path}?mode=ro", uri=True)
    try:
        rows = db.execute(
            "SELECT gap_id, channel, cause, status, start_ts_us, end_ts_us, recovery_origin"
            " FROM gaps ORDER BY start_ts_us"
        ).fetchall()
    finally:
        db.close()
    return [
        [gid, CHANNEL_NAMES.get(ch.partition("@")[2], ch), cause, status, _iso(s),
         round((e - s) / 1e6, 1), origin or ""]
        for gid, ch, cause, status, s, e, origin in rows
    ]  # fmt: skip


def _collection_start(state_path: Path) -> int | None:
    if not state_path.exists():
        return None
    db = sqlite3.connect(f"file:{state_path}?mode=ro", uri=True)
    try:
        row = db.execute("SELECT min(opened_ts_us) FROM sessions").fetchone()
    finally:
        db.close()
    return None if row is None or row[0] is None else int(row[0])


def _hourly(data_dir: Path, dataset: str, symbol: str) -> list[Path]:
    """Arquivos horários (`HH.parquet`); `archive.parquet` do backfill nunca entra."""
    root = data_dir / dataset / f"symbol={symbol}"
    return sorted(
        p for p in root.glob("date=*/*.parquet") if re.fullmatch(r"\d{2}\.parquet", p.name)
    )


def _hour_of(path: Path) -> int:
    day = datetime.strptime(path.parent.name.removeprefix("date="), "%Y-%m-%d").replace(tzinfo=UTC)
    return (int(day.timestamp()) + int(path.stem) * 3600) * 1_000_000


def _list_sum(column: Any) -> Any:
    values = pc.cast(pc.list_flatten(column), pa.float64())
    parents = pc.list_parent_indices(column)
    sums = (
        pa.table({"p": parents, "v": values}).group_by("p").aggregate([("v", "sum")]).sort_by("p")
    )
    return sums["v_sum"]


def _days(first: date, last: date) -> list[date]:
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def _iso(ts_us: int) -> str:
    return datetime.fromtimestamp(ts_us / 1e6, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _round(value: float | None, digits: int) -> float | str:
    return "" if value is None else round(value, digits)


def _write(path: Path, columns: list[str], rows: list[list[Any]]) -> int:
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows(rows)
    os.replace(tmp, path)
    return len(rows)
