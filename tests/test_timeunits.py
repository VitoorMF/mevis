from datetime import date

import pytest

from trader.core.timeunits import (
    MINUTE_US,
    TimestampRangeError,
    TimeUnit,
    archive_unit,
    floor_minute,
    to_us,
)

MS = 1_792_779_042_655
US = 1_792_779_042_655_963


def test_milliseconds_become_microseconds() -> None:
    assert to_us(MS, TimeUnit.MILLISECOND) == MS * 1000


def test_microseconds_pass_through() -> None:
    assert to_us(US, TimeUnit.MICROSECOND) == US


@pytest.mark.parametrize(
    ("value", "unit"),
    [(US, TimeUnit.MILLISECOND), (MS, TimeUnit.MICROSECOND), (0, TimeUnit.MICROSECOND)],
)
def test_value_outside_declared_unit_range_is_rejected(value: int, unit: TimeUnit) -> None:
    with pytest.raises(TimestampRangeError):
        to_us(value, unit)


def test_non_integer_timestamps_are_rejected() -> None:
    with pytest.raises(TimestampRangeError):
        to_us(True, TimeUnit.MICROSECOND)
    with pytest.raises(TimestampRangeError):
        to_us(float(US), TimeUnit.MICROSECOND)  # type: ignore[arg-type]


def test_archive_unit_switches_on_2025_01_01() -> None:
    assert archive_unit(date(2024, 12, 31)) is TimeUnit.MILLISECOND
    assert archive_unit(date(2025, 1, 1)) is TimeUnit.MICROSECOND


def test_floor_minute() -> None:
    start = 1_792_779_000_000_000
    assert start % MINUTE_US == 0
    assert floor_minute(start + 59_999_999) == start
    assert floor_minute(start) == start
