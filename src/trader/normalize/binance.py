"""Parsers dos formatos da Binance Spot: WebSocket, REST e arquivos públicos (doc 06 §5.4).

Funções puras: recebem payloads já decodificados (JSON ou linha CSV) e devolvem registros
canônicos. A unidade de tempo é sempre declarada por quem chama (RF-123).
"""

from collections.abc import Mapping, Sequence
from typing import Any

from trader.core.decimals import DecimalFormatError, exact_product, parse_decimal
from trader.core.models import (
    BookLevel,
    BookSnapshot,
    DepthDiffHeader,
    Kline,
    Origin,
    Trade,
    aggressor_side,
)
from trader.core.timeunits import TimestampRangeError, TimeUnit, to_us


class NormalizationError(ValueError):
    """Payload que não pode ser convertido em registro canônico (vai para a quarentena)."""


def unwrap_combined(message: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    """Separa `{"stream": ..., "data": ...}` dos streams combinados."""
    stream, data = message.get("stream"), message.get("data")
    if not isinstance(stream, str) or not isinstance(data, Mapping):
        raise NormalizationError("mensagem de stream combinado sem 'stream' ou 'data'")
    return stream, data


def symbol_from_stream(stream: str) -> str:
    """`btcusdt@depth20@100ms` → `BTCUSDT`."""
    symbol = stream.split("@", 1)[0]
    if not symbol.isalnum():
        raise NormalizationError(f"nome de stream inválido: {stream!r}")
    return symbol.upper()


def parse_ws_trade(
    data: Mapping[str, Any], *, recv_ts_us: int, unit: TimeUnit, provenance: str
) -> Trade:
    with _errors("trade"):
        _expect_event(data, "trade")
        price, qty = parse_decimal(data["p"]), parse_decimal(data["q"])
        is_buyer_maker = _bool(data["m"])
        return Trade(
            symbol=_str(data["s"]),
            trade_id=_int(data["t"]),
            price=price,
            qty=qty,
            quote_qty=exact_product(price, qty),
            trade_ts_us=to_us(_int(data["T"]), unit),
            event_ts_us=to_us(_int(data["E"]), unit),
            recv_ts_us=recv_ts_us,
            is_buyer_maker=is_buyer_maker,
            aggressor=aggressor_side(is_buyer_maker),
            origin=Origin.LIVE,
            provenance=provenance,
        )


def parse_rest_trade(
    row: Mapping[str, Any], *, symbol: str, unit: TimeUnit, provenance: str
) -> Trade:
    """Item de `/api/v3/historicalTrades` ou `/api/v3/trades`."""
    with _errors("trade REST"):
        return _historical_trade(
            symbol=symbol,
            trade_id=row["id"],
            price=row["price"],
            qty=row["qty"],
            quote_qty=row["quoteQty"],
            time=row["time"],
            is_buyer_maker=row["isBuyerMaker"],
            unit=unit,
            origin=Origin.REST,
            provenance=provenance,
        )


def parse_archive_trade(
    fields: Sequence[str], *, symbol: str, unit: TimeUnit, provenance: str
) -> Trade:
    """Linha CSV de `trades`: id, price, qty, quoteQty, time, isBuyerMaker, isBestMatch."""
    with _errors("trade de arquivo"):
        if len(fields) < 6:
            raise NormalizationError(f"linha com {len(fields)} campos")
        return _historical_trade(
            symbol=symbol,
            trade_id=fields[0],
            price=fields[1],
            qty=fields[2],
            quote_qty=fields[3],
            time=fields[4],
            is_buyer_maker=fields[5],
            unit=unit,
            origin=Origin.ARCHIVE,
            provenance=provenance,
        )


def parse_ws_kline(
    data: Mapping[str, Any], *, recv_ts_us: int, unit: TimeUnit, provenance: str
) -> Kline | None:
    """Candle do stream de klines; `None` enquanto o candle não estiver fechado (RF-102)."""
    with _errors("kline"):
        _expect_event(data, "kline")
        k = data["k"]
        if not _bool(k["x"]):
            return None
        return Kline(
            symbol=_str(k["s"]),
            interval=_str(k["i"]),
            open_ts_us=to_us(_int(k["t"]), unit),
            close_ts_us=to_us(_int(k["T"]), unit),
            open=parse_decimal(k["o"]),
            high=parse_decimal(k["h"]),
            low=parse_decimal(k["l"]),
            close=parse_decimal(k["c"]),
            volume=parse_decimal(k["v"]),
            quote_volume=parse_decimal(k["q"]),
            trade_count=_int(k["n"]),
            taker_buy_volume=parse_decimal(k["V"]),
            taker_buy_quote_volume=parse_decimal(k["Q"]),
            first_trade_id=_optional_id(k["f"]),
            last_trade_id=_optional_id(k["L"]),
            recv_ts_us=recv_ts_us,
            origin=Origin.LIVE,
            provenance=provenance,
        )


def parse_rest_kline(
    row: Sequence[Any],
    *,
    symbol: str,
    interval: str,
    unit: TimeUnit,
    origin: Origin,
    provenance: str,
) -> Kline:
    """Linha de `/api/v3/klines` ou do CSV de arquivos públicos (mesma ordem de colunas)."""
    if origin is Origin.LIVE:
        raise NormalizationError("klines REST ou de arquivo não podem ter origem live")
    with _errors("kline REST/arquivo"):
        if len(row) < 11:
            raise NormalizationError(f"linha com {len(row)} campos")
        return Kline(
            symbol=symbol,
            interval=interval,
            open_ts_us=to_us(_int(row[0]), unit),
            close_ts_us=to_us(_int(row[6]), unit),
            open=parse_decimal(row[1]),
            high=parse_decimal(row[2]),
            low=parse_decimal(row[3]),
            close=parse_decimal(row[4]),
            volume=parse_decimal(row[5]),
            quote_volume=parse_decimal(row[7]),
            trade_count=_int(row[8]),
            taker_buy_volume=parse_decimal(row[9]),
            taker_buy_quote_volume=parse_decimal(row[10]),
            first_trade_id=None,
            last_trade_id=None,
            recv_ts_us=None,
            origin=origin,
            provenance=provenance,
        )


def parse_ws_depth_snapshot(
    data: Mapping[str, Any], *, symbol: str, recv_ts_us: int, provenance: str
) -> BookSnapshot:
    """Profundidade parcial (`depth20@100ms`). O payload não traz o ativo nem horário (FV-07)."""
    with _errors("snapshot do livro"):
        return BookSnapshot(
            symbol=symbol,
            last_update_id=_int(data["lastUpdateId"]),
            recv_ts_us=recv_ts_us,
            bids=_levels(data["bids"]),
            asks=_levels(data["asks"]),
            provenance=provenance,
        )


def parse_ws_depth_diff_header(data: Mapping[str, Any], *, unit: TimeUnit) -> DepthDiffHeader:
    """Só o cabeçalho do diferencial, para verificar continuidade (RF-194)."""
    with _errors("diferencial do livro"):
        _expect_event(data, "depthUpdate")
        return DepthDiffHeader(
            symbol=_str(data["s"]),
            event_ts_us=to_us(_int(data["E"]), unit),
            first_update_id=_int(data["U"]),
            final_update_id=_int(data["u"]),
        )


def _historical_trade(
    *,
    symbol: str,
    trade_id: Any,
    price: Any,
    qty: Any,
    quote_qty: Any,
    time: Any,
    is_buyer_maker: Any,
    unit: TimeUnit,
    origin: Origin,
    provenance: str,
) -> Trade:
    parsed_price, parsed_qty = parse_decimal(price), parse_decimal(qty)
    computed = exact_product(parsed_price, parsed_qty)
    if parse_decimal(quote_qty) != computed:
        raise NormalizationError(f"quoteQty {quote_qty} difere de preço * quantidade ({computed})")
    maker = _bool(is_buyer_maker)
    return Trade(
        symbol=symbol,
        trade_id=_int(trade_id),
        price=parsed_price,
        qty=parsed_qty,
        quote_qty=computed,
        trade_ts_us=to_us(_int(time), unit),
        event_ts_us=None,
        recv_ts_us=None,
        is_buyer_maker=maker,
        aggressor=aggressor_side(maker),
        origin=origin,
        provenance=provenance,
    )


def _levels(raw: Any) -> tuple[BookLevel, ...]:
    if not isinstance(raw, list):
        raise NormalizationError("níveis do livro não são uma lista")
    levels = []
    for level in raw:
        if not isinstance(level, list) or len(level) != 2:
            raise NormalizationError(f"nível do livro inválido: {level!r}")
        levels.append((parse_decimal(level[0]), parse_decimal(level[1])))
    return tuple(levels)


def _expect_event(data: Mapping[str, Any], event: str) -> None:
    if data.get("e") != event:
        raise NormalizationError(f"evento {data.get('e')!r}, esperado {event!r}")


def _int(value: Any) -> int:
    """Inteiro vindo de JSON (int) ou de CSV (texto só com dígitos, com sinal opcional)."""
    if isinstance(value, bool):
        raise NormalizationError(f"booleano onde se esperava inteiro: {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    raise NormalizationError(f"inteiro inválido: {value!r}")


def _optional_id(value: Any) -> int | None:
    """A Binance usa -1 quando o candle não teve negócios."""
    parsed = _int(value)
    return None if parsed < 0 else parsed


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value in ("True", "true"):
        return True
    if value in ("False", "false"):
        return False
    raise NormalizationError(f"booleano inválido: {value!r}")


def _str(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise NormalizationError(f"texto inválido: {value!r}")
    return value


class _errors:
    """Converte erros de formato em `NormalizationError` com o nome do tipo de registro."""

    def __init__(self, what: str) -> None:
        self.what = what

    def __enter__(self) -> None:
        return None

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, _: object
    ) -> None:
        # Nunca suprime exceções: só troca erros de formato por NormalizationError.
        if isinstance(exc, (KeyError, TypeError, DecimalFormatError, TimestampRangeError)):
            raise NormalizationError(f"{self.what}: {exc!r}") from exc
