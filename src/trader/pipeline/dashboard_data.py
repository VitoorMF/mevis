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
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import zstandard

from trader.storage.s3cache import S3Cache, list_objects

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
_RAW_KEY = re.compile(
    r"^raw/[^/]+/[^/]+/(?P<channel>[^/]+)/date=(?P<date>\d{4}-\d{2}-\d{2})/hour=(?P<hour>\d{2})/"
)
_BACKFILL_OPS = re.compile(r"^derived/ops/backfill_files(-[a-z0-9]+)?\.parquet$")
RAW_COUNTS = ".raw-counts.json"
BUCKET_TOTALS = ".s3-totals.json"
SECONDS_PER_YEAR = 365.25 * 86_400


@dataclass
class SyncReport:
    downloaded: int = 0
    unchanged: int = 0
    raw_counted: int = 0
    """Arquivos brutos lidos para contar mensagens (os já contados ficam no cache)."""


@dataclass
class ExportReport:
    collection_start: str
    rows: dict[str, int] = field(default_factory=dict)


# ------------------------------------------------------------------ sincronização


def sync_from_s3(
    client: Any, bucket: str, data_dir: Path, symbol: str, today: date | None = None
) -> SyncReport:
    """Baixa para `data_dir` o que mudou no S3 desde a última vez (pelo ETag de cada objeto)."""
    cache = S3Cache(client, bucket, data_dir)
    cache.get(STATE_BACKUP)
    start = collection_start(data_dir / STATE_BACKUP)
    if start is not None:
        last = today or datetime.now(UTC).date()
        first = datetime.fromtimestamp(start / 1e6, UTC).date()
        for dataset in SYNCED:
            for day in _days(first, last):
                prefix = f"{dataset}/symbol={symbol}/date={day:%Y-%m-%d}/"
                for obj in cache.list(prefix):
                    if _HOURLY.search(obj["Key"]):
                        cache.get(obj["Key"], obj)
    for obj in cache.list("derived/ops/"):
        if _BACKFILL_OPS.match(obj["Key"]):
            cache.get(obj["Key"], obj)
    cache.save()
    report = SyncReport(downloaded=cache.downloaded, unchanged=cache.unchanged)
    _count_raw_in_s3(client, bucket, data_dir, symbol, report)
    objects = list(list_objects(client, bucket, ""))
    totals = {"objects": len(objects), "bytes": sum(int(o.get("Size", 0)) for o in objects)}
    (data_dir / BUCKET_TOTALS).write_text(json.dumps(totals))
    return report


def _count_raw_in_s3(
    client: Any, bucket: str, data_dir: Path, symbol: str, report: SyncReport
) -> None:
    """Conta as mensagens (linhas) de cada arquivo bruto do S3, guardando a contagem com o ETag."""
    path = data_dir / RAW_COUNTS
    try:
        counts: dict[str, dict[str, Any]] = json.loads(path.read_text())
    except (OSError, ValueError):
        counts = {}
    for obj in list_objects(client, bucket, f"raw/binance-spot/{symbol.lower()}/"):
        key = obj["Key"]
        match = _RAW_KEY.match(key)
        if match is None or counts.get(key, {}).get("etag") == obj["ETag"]:
            continue
        body = client.get_object(Bucket=bucket, Key=key)["Body"]
        lines = 0
        reader = zstandard.ZstdDecompressor().stream_reader(body)
        while chunk := reader.read(1 << 20):
            lines += chunk.count(b"\n")
        counts[key] = {
            "etag": obj["ETag"],
            "channel": match["channel"],
            "hour": _hour_us(match["date"], match["hour"]),
            "lines": lines,
        }
        report.raw_counted += 1
    path.write_text(json.dumps(counts, indent=1, sort_keys=True))


# ------------------------------------------------------------------ agregação


def export(data_dir: Path, out_dir: Path, symbol: str) -> ExportReport:
    """Grava os CSVs de doc 06 §8.4 em `out_dir` a partir de `data_dir`."""
    state_path = data_dir / "state.db"
    if not state_path.exists():
        state_path = data_dir / STATE_BACKUP
    start = collection_start(state_path)
    if start is None:
        raise ValueError(f"nenhuma sessão de coleta em {state_path}")
    first_minute = start // MINUTE_US * MINUTE_US + MINUTE_US  # primeiro minuto completo
    out_dir.mkdir(parents=True, exist_ok=True)
    report = ExportReport(collection_start=_iso(start))

    minutes: dict[int, dict[str, float]] = defaultdict(dict)
    delays: dict[int, tuple[int, float, float]] = {}
    live = _LiveTrades()
    _klines(data_dir, symbol, first_minute, minutes)
    _trades(data_dir, symbol, first_minute, minutes, delays, live)
    last_book = _book(data_dir, symbol, first_minute, minutes)

    columns = [
        "minute", "open", "high", "low", "close", "volume", "buy_volume", "sell_volume", "trades",
        "snapshots", "spread_bps", "imbalance_top20", "imbalance_level1",
    ]  # fmt: skip
    rows = []
    for minute in sorted(minutes):
        d = minutes[minute]
        if "close" not in d:
            continue  # minuto ainda sem candle fechado
        rows.append([
            _iso(minute), d["open"], d["high"], d["low"], d["close"], round(d["volume"], 5),
            round(d.get("buy", 0.0), 5),
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
    report.rows["highlights.csv"] = _write(
        out_dir / "highlights.csv",
        ["metric", "value", "detail"],
        _highlights(data_dir, symbol, state_path, start, live),
    )
    return report


@dataclass
class _LiveTrades:
    """Totais dos negócios ao vivo, acumulados arquivo a arquivo."""

    count: int = 0
    usd: float = 0.0
    biggest: dict[str, Any] | None = None
    busiest: tuple[int, int, Path] | None = None
    """(negócios, segundo em µs, arquivo da hora)."""
    delays: list[Any] = field(default_factory=list)


def _highlights(
    data_dir: Path, symbol: str, state_path: Path, start: int, live: _LiveTrades
) -> list[list[Any]]:
    rows: list[list[Any]] = [["collection_start", _iso(start), "início da coleta ao vivo (UTC)"]]

    raw = _raw_counts(data_dir, symbol)
    if raw:
        by_channel: dict[str, int] = defaultdict(int)
        for channel, _, lines in raw:
            by_channel[channel] += lines
        end = max(hour for _, hour, _ in raw) + HOUR_US
        seconds = (end - start) / 1e6
        total = sum(by_channel.values())
        names = {
            "btcusdt@trade": "messages_trades",
            "btcusdt@depth20@100ms": "messages_book_top20",
            "btcusdt@depth@100ms": "messages_book_diff",
            "btcusdt@kline_1m": "messages_klines",
        }
        rows.append(["window_seconds", round(seconds), f"até {_iso(end)} (UTC)"])
        rows.append(["messages_total", total, "mensagens recebidas, WebSocket e REST"])
        rows.append(["messages_per_second", round(total / seconds, 1), "média do período"])
        for channel, metric in names.items():
            count = by_channel.get(channel.replace("btcusdt", symbol.lower()), 0)
            rows.append([metric, count, f"{round(count / seconds, 1)} por segundo"])
        rest = sum(v for k, v in by_channel.items() if k.startswith("rest_"))
        rows.append(["messages_rest", rest, "consultas REST (livro completo, horário)"])

    if live.count:
        rows.append(["live_trades", live.count, "negócios ao vivo, sem repetições"])
        rows.append(["volume_usd", round(live.usd, 2), "soma de preço vezes quantidade (USDT)"])
        rows.append(["avg_trade_usd", round(live.usd / live.count, 2), "média por negócio"])
    if live.biggest is not None:
        b = live.biggest
        rows.append(["biggest_trade_usd", round(b["usd"], 2), f"negócio {b['trade_id']}"])
        rows.append(["biggest_trade_btc", b["btc"], f"negócio {b['trade_id']}"])
        rows.append(["biggest_trade_price", b["price"], f"negócio {b['trade_id']}"])
        rows.append(["biggest_trade_at", _iso(b["ts"]), "UTC"])
    if live.busiest is not None:
        count, second, path = live.busiest
        table = pq.read_table(path, columns=["trade_ts_us", "price", "is_buyer_maker"])
        table = table.filter(
            pc.equal(pc.divide(table["trade_ts_us"], 1_000_000), second // 1_000_000)
        )
        prices = pc.cast(table["price"], pa.float64())
        sellers = pc.sum(pc.cast(table["is_buyer_maker"], pa.int64())).as_py()
        rows.append(["busiest_second_trades", count, "negócios no mesmo segundo"])
        rows.append(["busiest_second_at", _iso(second), "UTC"])
        rows.append(["busiest_second_low", pc.min(prices).as_py(), "menor preço no segundo"])
        rows.append(["busiest_second_high", pc.max(prices).as_py(), "maior preço no segundo"])
        rows.append(["busiest_second_seller_initiated", sellers, "vendedor tomou a iniciativa"])
    if live.delays:
        delays = pa.chunked_array(live.delays)
        p50, p99 = pc.quantile(delays, q=[0.5, 0.99]).to_pylist()
        rows.append(["delay_p50_ms", round(p50, 1), "do negócio na Binance ao recebimento"])
        rows.append(["delay_p99_ms", round(p99, 1), "do negócio na Binance ao recebimento"])

    db = sqlite3.connect(f"file:{state_path}?mode=ro", uri=True)
    try:
        received, stored = db.execute(
            "SELECT sum(original_size), sum(compressed_size) FROM raw_files"
        ).fetchone()
    finally:
        db.close()
    if received:
        rows.append(["raw_received_bytes", received, "camada bruta, descomprimida"])
        rows.append(["raw_stored_bytes", stored, "camada bruta, comprimida (zstd)"])

    for dataset, (files, total, first_day, last_day) in sorted(history_counts(data_dir).items()):
        rows.append([f"history_{dataset}_rows", total, f"{files} arquivos públicos importados"])
        rows.append([f"history_{dataset}_first_day", first_day, ""])
        rows.append([f"history_{dataset}_last_day", last_day, ""])

    try:
        totals = json.loads((data_dir / BUCKET_TOTALS).read_text())
        rows.append(["bucket_bytes", totals["bytes"], "tudo o que está no S3"])
        rows.append(["bucket_objects", totals["objects"], "arquivos no S3"])
    except (OSError, ValueError, KeyError):
        pass
    return rows


def _raw_counts(data_dir: Path, symbol: str) -> list[tuple[str, int, int]]:
    """(canal, hora em µs, linhas) por arquivo bruto: do cache do S3 ou dos arquivos locais."""
    try:
        cached = json.loads((data_dir / RAW_COUNTS).read_text())
        return [(c["channel"], c["hour"], c["lines"]) for c in cached.values()]
    except (OSError, ValueError, KeyError):
        pass
    counts = []
    root = data_dir / "raw" / "binance-spot" / symbol.lower()
    for path in sorted(root.glob("*/date=*/hour=*/*")):
        if path.name.endswith(".tmp"):
            continue
        hour = _hour_us(path.parent.parent.name.removeprefix("date="), path.parent.name[5:])
        if path.name.endswith(".zst"):
            with path.open("rb") as handle:
                reader = zstandard.ZstdDecompressor().stream_reader(handle)
                lines = 0
                while chunk := reader.read(1 << 20):
                    lines += chunk.count(b"\n")
        else:
            lines = path.read_bytes().count(b"\n")
        counts.append((path.parent.parent.parent.name, hour, lines))
    return counts


def history_counts(data_dir: Path) -> dict[str, tuple[int, int, str, str]]:
    """Por dataset: arquivos importados, linhas, primeiro e último dia (só contagens)."""
    result: dict[str, tuple[int, int, str, str]] = {}
    for path in sorted((data_dir / "derived" / "ops").glob("backfill_files*.parquet")):
        table = pq.read_table(path)
        if table.num_rows == 0 or pa.types.is_null(table.schema.field("dataset").type):
            continue
        for row in table.to_pylist():
            if row["status"] != "imported":
                continue
            files, total, first, last = result.get(row["dataset"], (0, 0, "9999", "0000"))
            result[row["dataset"]] = (
                files + 1,
                total + int(row["rows"]),
                min(first, row["first_day"]),
                max(last, row["last_day"]),
            )
    return result


def _klines(data_dir: Path, symbol: str, first: int, minutes: dict[int, dict[str, float]]) -> None:
    for path in hourly_files(data_dir, "normalized/klines_1m", symbol):
        columns = ["open_ts_us", "open", "high", "low", "close", "volume"]
        table = pq.read_table(path, columns=columns)
        for open_ts, o, h, low, c, volume in zip(
            *(table[name].to_pylist() for name in columns), strict=True
        ):
            if open_ts >= first:
                minutes[open_ts].update(
                    open=float(o),
                    high=float(h),
                    low=float(low),
                    close=float(c),
                    volume=float(volume),
                )


def _trades(
    data_dir: Path,
    symbol: str,
    first: int,
    minutes: dict[int, dict[str, float]],
    delays: dict[int, tuple[int, float, float]],
    live: _LiveTrades,
) -> None:
    columns = ["trade_id", "trade_ts_us", "price", "qty", "quote_qty", "aggressor", "recv_ts_us"]
    for path in hourly_files(data_dir, "normalized/trades", symbol):
        table = pq.read_table(path, columns=columns)
        table = table.filter(pc.greater_equal(table["trade_ts_us"], first))
        if table.num_rows == 0:
            continue
        _accumulate(path, table, live)
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
        received = table.filter(pc.is_valid(table["recv_ts_us"]))
        if received.num_rows:
            delay_ms = pc.divide(
                pc.cast(pc.subtract(received["recv_ts_us"], received["trade_ts_us"]), pa.float64()),
                1000.0,
            )
            live.delays.append(delay_ms)
            p50, p99 = pc.quantile(delay_ms, q=[0.5, 0.99]).to_pylist()
            hour = int(hour_of(path))
            delays[hour] = (received.num_rows, round(p50, 1), round(p99, 1))


def _accumulate(path: Path, table: Any, live: _LiveTrades) -> None:
    """Soma, maior negócio e segundo mais movimentado (cada segundo cabe num arquivo de hora)."""
    usd = pc.cast(table["quote_qty"], pa.float64())
    live.count += table.num_rows
    live.usd += pc.sum(usd).as_py()
    i = pc.index(usd, pc.max(usd)).as_py()
    if live.biggest is None or usd[i].as_py() > live.biggest["usd"]:
        live.biggest = {
            "usd": usd[i].as_py(),
            "btc": float(table["qty"][i].as_py()),
            "price": float(table["price"][i].as_py()),
            "ts": table["trade_ts_us"][i].as_py(),
            "trade_id": table["trade_id"][i].as_py(),
        }
    seconds = pa.table({"s": pc.divide(table["trade_ts_us"], 1_000_000)})
    grouped = seconds.group_by("s").aggregate([("s", "count")]).sort_by([("s_count", "descending")])
    top, count = grouped["s"][0].as_py(), grouped["s_count"][0].as_py()
    if live.busiest is None or count > live.busiest[0]:
        live.busiest = (count, top * 1_000_000, path)


def _book(
    data_dir: Path, symbol: str, first: int, minutes: dict[int, dict[str, float]]
) -> dict[str, Any] | None:
    last = None
    for path in hourly_files(data_dir, "normalized/book_top20", symbol):
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
    for path in hourly_files(data_dir, "derived/quality_mask", symbol):
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


def collection_start(state_path: Path) -> int | None:
    if not state_path.exists():
        return None
    db = sqlite3.connect(f"file:{state_path}?mode=ro", uri=True)
    try:
        row = db.execute("SELECT min(opened_ts_us) FROM sessions").fetchone()
    finally:
        db.close()
    return None if row is None or row[0] is None else int(row[0])


def hourly_files(data_dir: Path, dataset: str, symbol: str) -> list[Path]:
    """Arquivos horários (`HH.parquet`); `archive.parquet` do backfill nunca entra."""
    root = data_dir / dataset / f"symbol={symbol}"
    return sorted(
        p for p in root.glob("date=*/*.parquet") if re.fullmatch(r"\d{2}\.parquet", p.name)
    )


def hour_of(path: Path) -> int:
    day = datetime.strptime(path.parent.name.removeprefix("date="), "%Y-%m-%d").replace(tzinfo=UTC)
    return (int(day.timestamp()) + int(path.stem) * 3600) * 1_000_000


def _list_sum(column: Any) -> Any:
    values = pc.cast(pc.list_flatten(column), pa.float64())
    parents = pc.list_parent_indices(column)
    sums = (
        pa.table({"p": parents, "v": values}).group_by("p").aggregate([("v", "sum")]).sort_by("p")
    )
    return sums["v_sum"]


def _hour_us(day: str, hour: str) -> int:
    start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC)
    return (int(start.timestamp()) + int(hour) * 3600) * 1_000_000


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
