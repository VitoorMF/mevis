"""Decimais exatos para preços e quantidades (RN-011; doc 06 §5.3)."""

from decimal import Decimal, InvalidOperation

SCALE = 8
"""Casas decimais da fonte (doc 06 MD-12) e da camada normalizada."""

_QUANTUM = Decimal(1).scaleb(-SCALE)
_PRICE_QTY_LIMIT = Decimal(10) ** (18 - SCALE)  # DECIMAL(18,8)


class DecimalFormatError(ValueError):
    """Texto que não representa um decimal exato e compatível com o esquema."""


def parse_decimal(text: str) -> Decimal:
    """Converte o texto da exchange em `Decimal` com 8 casas, sem arredondar.

    Rejeita valores não finitos, com mais de 8 casas significativas ou que não cabem em
    DECIMAL(18,8).
    """
    if not isinstance(text, str):
        raise DecimalFormatError(f"esperado texto, recebido {type(text).__name__}")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise DecimalFormatError(f"decimal inválido: {text!r}") from exc
    if not value.is_finite():
        raise DecimalFormatError(f"decimal não finito: {text!r}")
    return _fit(value, _PRICE_QTY_LIMIT, text)


def exact_product(price: Decimal, qty: Decimal) -> Decimal:
    """`price * qty` com 8 casas; falha em vez de arredondar se o produto tiver mais casas."""
    return _fit(price * qty, None, f"{price} * {qty}")


def _fit(value: Decimal, limit: Decimal | None, source: str) -> Decimal:
    try:
        quantized = value.quantize(_QUANTUM)
    except InvalidOperation as exc:
        raise DecimalFormatError(f"valor fora da precisão suportada: {source}") from exc
    if quantized != value:
        raise DecimalFormatError(f"mais de {SCALE} casas decimais: {source}")
    if limit is not None and abs(quantized) >= limit:
        raise DecimalFormatError(f"valor não cabe em DECIMAL(18,{SCALE}): {source}")
    return quantized
