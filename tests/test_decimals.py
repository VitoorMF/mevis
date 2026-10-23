from decimal import Decimal

import pytest

from trader.core.decimals import DecimalFormatError, exact_product, parse_decimal


def test_parses_exchange_text_exactly() -> None:
    value = parse_decimal("81524.01000000")
    assert value == Decimal("81524.01")
    assert str(value) == "81524.01000000"


def test_normalizes_to_eight_places() -> None:
    assert str(parse_decimal("1.5")) == "1.50000000"
    assert str(parse_decimal("1.0000000000")) == "1.00000000"


@pytest.mark.parametrize(
    "text", ["0.000000001", "NaN", "Infinity", "abc", "", "10000000000", "1e30"]
)
def test_rejects_values_that_do_not_fit_the_schema(text: str) -> None:
    with pytest.raises(DecimalFormatError):
        parse_decimal(text)


def test_rejects_non_text_input() -> None:
    with pytest.raises(DecimalFormatError):
        parse_decimal(1.5)  # type: ignore[arg-type]


def test_exact_product() -> None:
    product = exact_product(Decimal("81524.01"), Decimal("0.00008"))
    assert product == Decimal("6.5219208")


def test_product_with_more_than_eight_places_fails_instead_of_rounding() -> None:
    with pytest.raises(DecimalFormatError):
        exact_product(Decimal("0.00000001"), Decimal("0.5"))
