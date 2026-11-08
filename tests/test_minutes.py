"""Montagem da série de minutos e comando `trader features` (RF-203, RF-204, RF-212, RF-213)."""

import json
import subprocess
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from conftest import FakeS3
from trader.aggregate.schema import BINS, MINUTE_BOOK, MINUTE_TRADES
from trader.cli.main import main
from trader.core.holdout import HoldoutRefused
from trader.core.timeunits import MINUTE_US, SECOND_US
from trader.features.compute import FEATURES, catalog_id
from trader.features.series import BOOK_FIELDS, MINUTES
from trader.pipeline.minutes import (
    EXEC_COLUMNS,
    CachedS3Source,
    LocalSource,
    Minutes,
    assemble_minutes,
    day_start_us,
)
from trader.storage.parquet import KLINES_1M, QUALITY_MASK
from trader.storage.s3cache import S3Cache

SYMBOL = "BTCUSDT"
KLINE = "btcusdt@kline_1m"
LIVE_DAY, ARCHIVE_DAY = date(2026, 10, 23), date(2026, 10, 24)
LIVE_START = day_start_us(LIVE_DAY)
ARCHIVE_START = day_start_us(ARCHIVE_DAY)


def at(hour: int, minute: int, day: date = LIVE_DAY) -> int:
    return day_start_us(day) + (hour * 60 + minute) * MINUTE_US


def row_of(ts: int) -> int:
    return (ts - LIVE_START) // MINUTE_US


def price(ts: int) -> Decimal:
    return Decimal(60_000) + Decimal(ts // MINUTE_US % 1000) / 100 + Decimal("0.00000001")


def kline_rows(
    minutes: list[int], recv: dict[int, int | None], origin: str
) -> list[dict[str, Any]]:
    rows = []
    for ts in minutes:
        rows.append(
            {
                "open_ts_us": ts,
                "close_ts_us": ts + MINUTE_US - 1000,
                "open": price(ts),
                "high": price(ts) + 1,
                "low": price(ts) - 1,
                "close": price(ts),
                "volume": Decimal("12.5"),
                "quote_volume": Decimal("750000"),
                "trade_count": 1000,
                "taker_buy_volume": Decimal("6"),
                "taker_buy_quote_volume": Decimal("360000"),
                "first_trade_id": None,
                "last_trade_id": None,
                "recv_ts_us": recv.get(ts, ts + MINUTE_US + 500_000),
                "symbol": SYMBOL,
                "origin": origin,
                "provenance": "teste",
                "schema_version": 1,
            }
        )
    return rows


def trade_rows(
    minutes: list[int], status: dict[int, str], recv: dict[int, int | None], source: str
) -> list[dict[str, Any]]:
    return [
        {
            "symbol": SYMBOL,
            "minute_ts_us": ts,
            "trade_count": 10,
            "buy_count": 4,
            "volume": Decimal("2.5"),
            "buy_volume": Decimal("1"),
            "quote_volume": Decimal("150000"),
            "buy_quote_volume": Decimal("60000"),
            "open": price(ts),
            "high": price(ts),
            "low": price(ts),
            "close": price(ts),
            "first_trade_id": 1,
            "last_trade_id": 10,
            "last_recv_ts_us": recv.get(ts, None if source == "archive" else ts + MINUTE_US),
            "size_bins_count": [0] * (BINS - 1) + [10],
            "size_bins_volume": [Decimal(0)] * (BINS - 1) + [Decimal("2.5")],
            **execution(ts),
            "status": status.get(ts, "ok"),
            "source": source,
            "schema_version": 2,
        }
        for ts in minutes
    ]


def execution(ts: int) -> dict[str, Any]:
    if ts == NO_EXECUTION:
        return dict.fromkeys(EXEC_COLUMNS)
    return {
        "exec_price_2500ms": price(ts) + Decimal("0.5"),
        "exec_ts_2500ms_us": ts + 2_600_000,
        "exec_price_4000ms": price(ts) + 1,
        "exec_ts_4000ms_us": ts + 4_100_000,
    }


def book_rows(minutes: list[int], status: dict[int, str]) -> list[dict[str, Any]]:
    return [
        {
            "symbol": SYMBOL,
            "minute_ts_us": ts,
            "snapshot_count": 600,
            "last_recv_ts_us": ts + MINUTE_US - 50_000,
            **{name: 0.5 for name in BOOK_FIELDS},
            "status": status.get(ts, "ok"),
            "schema_version": 1,
        }
        for ts in minutes
    ]


def mask_rows(minutes: list[int], channel: str, status: dict[int, str]) -> list[dict[str, Any]]:
    return [
        {
            "symbol": SYMBOL,
            "channel": channel,
            "minute_ts_us": ts,
            "status": status.get(ts, "ok"),
            "details": "",
            "schema_version": 1,
        }
        for ts in minutes
    ]


def write(
    root: Path, dataset: str, day: date, name: str, schema: pa.Schema, rows: list[Any]
) -> None:
    path = root / dataset / f"symbol={SYMBOL}" / f"date={day:%Y-%m-%d}" / f"{name}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)


def hour_minutes(hour: int, day: date = LIVE_DAY) -> list[int]:
    return [at(hour, m, day) for m in range(60)]


MISSING_CANDLE = at(22, 10)
RECOVERED_CANDLE = at(22, 20)
LATE_CANDLE = at(22, 30)
RECOVERED_STATUS = at(22, 31)
MISSING_TRADES = at(22, 40)
NO_RECV_TRADES = at(22, 50)
INVALID_BOOK = at(22, 5)
EXCEPTION = int(datetime(2026, 10, 23, 22, 44, tzinfo=UTC).timestamp()) * SECOND_US
ARCHIVE_GAP = at(3, 0, ARCHIVE_DAY)
ARCHIVE_MISSING_TRADES = at(4, 0, ARCHIVE_DAY)
NO_EXECUTION = at(22, 55)


def build_data(root: Path) -> Path:
    """23/10 ao vivo (horas 22 e 23; negócios e livro só na 22) e 24/10 de arquivo."""
    h22, h23 = hour_minutes(22), hour_minutes(23)
    candles22 = [ts for ts in h22 if ts != MISSING_CANDLE]
    write(
        root,
        "normalized/klines_1m",
        LIVE_DAY,
        "22",
        KLINES_1M,
        kline_rows(candles22, {RECOVERED_CANDLE: None}, "live"),
    )
    write(root, "normalized/klines_1m", LIVE_DAY, "23", KLINES_1M, kline_rows(h23, {}, "live"))
    for hour, minutes in ((22, h22), (23, h23)):
        rows = mask_rows(minutes, KLINE, {LATE_CANDLE: "late", RECOVERED_STATUS: "recovered"})
        rows += mask_rows(minutes, "btcusdt@trade", dict.fromkeys(minutes, "missing"))
        write(root, "derived/quality_mask", LIVE_DAY, f"{hour:02d}", QUALITY_MASK, rows)
    write(
        root,
        "derived/minute_trades",
        LIVE_DAY,
        "22",
        MINUTE_TRADES,
        trade_rows(h22, {MISSING_TRADES: "missing"}, {NO_RECV_TRADES: None}, "live"),
    )
    write(
        root,
        "derived/minute_book",
        LIVE_DAY,
        "22",
        MINUTE_BOOK,
        book_rows(h22, {INVALID_BOOK: "invalid"}),
    )

    day = [ARCHIVE_START + i * MINUTE_US for i in range(1440)]
    write(
        root,
        "normalized/klines_1m",
        ARCHIVE_DAY,
        "archive",
        KLINES_1M,
        kline_rows([ts for ts in day if ts != ARCHIVE_GAP], dict.fromkeys(day), "archive"),
    )
    write(
        root,
        "derived/minute_trades",
        ARCHIVE_DAY,
        "archive",
        MINUTE_TRADES,
        trade_rows(day, {ARCHIVE_MISSING_TRADES: "missing"}, {}, "archive"),
    )
    live = kline_rows(hour_minutes(0, ARCHIVE_DAY), {}, "live")
    for row in live:
        row["close"] = Decimal(1)
    write(root, "normalized/klines_1m", ARCHIVE_DAY, "00", KLINES_1M, live)  # o arquivo prevalece
    return root


def assemble(root: Path, start: date = LIVE_DAY, end: date = ARCHIVE_DAY, **kw: Any) -> Minutes:
    return assemble_minutes(
        LocalSource(root), symbol=SYMBOL, kline_channel=KLINE, start=start, end=end, **kw
    )


def column(minutes: Minutes, name: str) -> list[Any]:
    return list(minutes.table[name].to_pylist())


def test_one_row_per_minute_with_decimals_kept(tmp_path: Path) -> None:
    minutes = assemble(build_data(tmp_path))
    table = minutes.table
    assert table.schema == MINUTES
    assert table.num_rows == 2 * 1440
    assert column(minutes, "minute_ts_us") == [LIVE_START + i * MINUTE_US for i in range(2880)]
    assert table.schema.field("candle_close").type == pa.decimal128(18, 8)
    assert column(minutes, "candle_close")[row_of(at(22, 0))] == price(at(22, 0))
    assert minutes.archive_days == {
        "klines_1m": 1,
        "minute_trades": 1,
        "minute_book": 0,
        "quality_mask": 0,
    }
    assert minutes.live_hours == {
        "klines_1m": 2,
        "minute_trades": 1,
        "minute_book": 1,
        "quality_mask": 2,
    }
    assert len(minutes.inputs) == 8 and all(len(i["sha256"]) == 64 for i in minutes.inputs)


def test_live_minutes_take_status_and_receipt_time(tmp_path: Path) -> None:
    minutes = assemble(build_data(tmp_path))
    valid, ready = column(minutes, "candle_valid"), column(minutes, "candle_available_us")
    close = column(minutes, "candle_close")
    first = row_of(at(22, 0))
    assert valid[first] and ready[first] == at(22, 1) + 500_000
    assert not valid[row_of(at(0, 0))] and ready[row_of(at(0, 0))] is None, "sem arquivo"
    assert not valid[row_of(MISSING_CANDLE)] and close[row_of(MISSING_CANDLE)] is None
    recovered = row_of(RECOVERED_CANDLE)
    assert valid[recovered] and ready[recovered] == RECOVERED_CANDLE + MINUTE_US + SECOND_US
    assert not valid[row_of(LATE_CANDLE)] and close[row_of(LATE_CANDLE)] is not None
    assert valid[row_of(RECOVERED_STATUS)]

    trades, trades_ready = column(minutes, "trades_valid"), column(minutes, "trades_available_us")
    assert trades[first] and trades_ready[first] == at(22, 1)
    assert not trades[row_of(MISSING_TRADES)]
    assert trades_ready[row_of(NO_RECV_TRADES)] == NO_RECV_TRADES + MINUTE_US + SECOND_US
    assert not any(trades[row_of(at(23, 0)) : row_of(at(23, 59)) + 1]), "hora sem agregado"

    book, book_ready = column(minutes, "book_valid"), column(minutes, "book_available_us")
    assert book[first] and book_ready[first] == at(22, 1)
    assert not book[row_of(INVALID_BOOK)]
    assert column(minutes, "book_spread_rel_mean")[first] == 0.5


def test_recorded_exception_minute_is_invalid_in_trades_and_book(tmp_path: Path) -> None:
    minutes = assemble(build_data(tmp_path))
    row = row_of(EXCEPTION)
    assert column(minutes, "candle_valid")[row]
    assert not column(minutes, "trades_valid")[row]
    assert not column(minutes, "book_valid")[row]
    assert column(minutes, "trades_valid")[row - 1] and column(minutes, "book_valid")[row - 1]


def test_archive_days_are_available_one_second_after_the_minute(tmp_path: Path) -> None:
    minutes = assemble(build_data(tmp_path))
    first = row_of(ARCHIVE_START)
    assert column(minutes, "candle_valid")[first]
    assert column(minutes, "candle_close")[first] == price(ARCHIVE_START), "arquivo, não ao vivo"
    assert column(minutes, "candle_available_us")[first] == ARCHIVE_START + MINUTE_US + SECOND_US
    assert column(minutes, "trades_available_us")[first] == ARCHIVE_START + MINUTE_US + SECOND_US
    assert not column(minutes, "candle_valid")[row_of(ARCHIVE_GAP)]
    assert not column(minutes, "trades_valid")[row_of(ARCHIVE_MISSING_TRADES)]
    assert not any(column(minutes, "book_valid")[first:]), "o arquivo não tem livro"


def test_complete_day_skips_the_copy_with_the_same_result(tmp_path: Path) -> None:
    """Com uma linha por minuto em ordem, a montagem usa as colunas lidas sem copiar."""
    root = build_data(tmp_path)
    both = assemble(root)
    archive = assemble(root, start=ARCHIVE_DAY)
    assert archive.table.equals(both.table.slice(1440))


def test_execution_prices_come_apart_from_the_series(tmp_path: Path) -> None:
    """RF-221: o preço de execução vai para uma tabela à parte, nunca para `MINUTES`."""
    root = build_data(tmp_path)
    plain = assemble(root)
    minutes = assemble(root, execution=True)
    assert plain.execution is None
    assert minutes.table.equals(plain.table)
    assert not set(EXEC_COLUMNS) & set(MINUTES.names)
    prices = minutes.execution
    assert prices is not None
    assert prices.column_names == ["minute_ts_us", *EXEC_COLUMNS]
    assert prices["minute_ts_us"].equals(minutes.table["minute_ts_us"])
    first = at(22, 0)
    assert prices.slice(row_of(first), 1).to_pylist()[0] == {
        "minute_ts_us": first,
        "exec_price_2500ms": price(first) + Decimal("0.5"),
        "exec_ts_2500ms_us": first + 2_600_000,
        "exec_price_4000ms": price(first) + 1,
        "exec_ts_4000ms_us": first + 4_100_000,
    }
    base = prices["exec_price_2500ms"].to_pylist()
    assert base[row_of(NO_EXECUTION)] is None
    assert base[row_of(at(23, 0))] is None, "hora sem agregado"
    assert base[row_of(ARCHIVE_START)] == price(ARCHIVE_START) + Decimal("0.5")


def test_old_trade_aggregates_are_refused_for_execution(tmp_path: Path) -> None:
    """Agregados da versão 1, sem preço de execução, servem às features, mas não ao alvo."""
    root = build_data(tmp_path)
    path = root / "derived/minute_trades" / f"symbol={SYMBOL}" / "date=2026-10-24/archive.parquet"
    pq.write_table(pq.read_table(path).drop_columns(list(EXEC_COLUMNS)), path)
    assert assemble(root).table.num_rows == 2880
    with pytest.raises(ValueError, match=r"sem as colunas exec_price_2500ms.*RF-222"):
        assemble(root, execution=True)


def test_holdout_days_are_refused_unless_final_evaluation(tmp_path: Path) -> None:
    root = build_data(tmp_path)
    with pytest.raises(HoldoutRefused, match="RF-204"):
        assemble(root, start=date(2026, 10, 22))
    minutes = assemble(root, start=date(2026, 10, 22), allow_holdout=True)
    assert (minutes.holdout_days, minutes.allow_holdout) == (1, True)
    assert "klines_1m: 1 dia(s) do período sem arquivo" in minutes.warnings


def test_repeated_minute_is_an_error(tmp_path: Path) -> None:
    root = build_data(tmp_path)
    minutes = hour_minutes(22)
    write(root, "derived/minute_book", LIVE_DAY, "23", MINUTE_BOOK, book_rows(minutes[:1], {}))
    with pytest.raises(ValueError, match="minuto repetido"):
        assemble(root)


def test_s3_source_downloads_only_what_changed(tmp_path: Path) -> None:
    root = build_data(tmp_path / "data")
    objects = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*.parquet")}
    s3 = FakeS3(objects)
    local = assemble(root)

    def from_s3() -> tuple[Minutes, S3Cache]:
        cache = S3Cache(s3, "bucket", tmp_path / "cache")
        minutes = assemble_minutes(
            CachedS3Source(cache),
            symbol=SYMBOL,
            kline_channel=KLINE,
            start=LIVE_DAY,
            end=ARCHIVE_DAY,
        )
        cache.save()
        return minutes, cache

    first, cache = from_s3()
    assert first.table.equals(local.table)
    assert cache.downloaded == 8, "o arquivo horário do dia de arquivo não é baixado"
    second, cache = from_s3()
    assert (cache.downloaded, cache.unchanged) == (0, 8)
    assert second.table.equals(local.table)


def test_features_command_writes_parquet_with_catalog(tmp_path: Path) -> None:
    root = build_data(tmp_path / "data")
    out = tmp_path / "out" / "features.parquet"
    args = ["features", "--data-dir", str(root), "--start", "2026-10-23", "--end", "2026-10-24"]
    assert main([*args, "--out", str(out)]) == 0
    table = pq.read_table(out)
    assert table.num_rows == 2880
    assert table.column_names[2 : 2 + len(FEATURES)] == [name for name, _ in FEATURES]
    metadata = json.loads(table.schema.metadata[b"mevis.features"])
    assert metadata["catalog_id"] == catalog_id()
    assert (metadata["allow_holdout"], metadata["holdout_days"]) == (False, 0)
    assert metadata["inputs"] == 8
    assert len(metadata["catalog"]["features"]) == 59

    refused = ["features", "--data-dir", str(root), "--start", "2026-10-22", "--end", "2026-10-23"]
    assert main([*refused, "--out", str(tmp_path / "x.parquet")]) == 2
    assert not (tmp_path / "x.parquet").exists()


def test_cli_does_not_need_numpy() -> None:
    """O host não tem numpy, xgboost nem scikit-learn (RNF-029): a CLI importa a pesquisa só no
    comando."""
    code = (
        "import sys; sys.modules['numpy'] = None\n"
        "import trader.cli.main\n"
        "assert not any(m.startswith('trader.features') for m in sys.modules)\n"
        "assert 'trader.pipeline.minutes' not in sys.modules\n"
        "assert not any(m.startswith('trader.datasets') for m in sys.modules)\n"
        "assert 'trader.pipeline.datasets' not in sys.modules\n"
        "assert not any(m.startswith('trader.backtest') for m in sys.modules)\n"
        "assert 'trader.pipeline.baselines' not in sys.modules\n"
        "assert not any(m.startswith(('trader.models', 'trader.stats')) for m in sys.modules)\n"
        "assert 'trader.pipeline.walkforward' not in sys.modules\n"
        "assert 'trader.pipeline.report' not in sys.modules\n"
        "assert not any(m.split('.')[0] in ('xgboost', 'sklearn') for m in sys.modules)\n"
        "import trader.experiments.registry\n"
        "import trader.pipeline.research\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
