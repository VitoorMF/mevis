from trader.core.timeunits import MINUTE_US
from trader.quality.mask import ClockReference, Flag, GapSpan, MaskStatus, build_mask, worst

HOUR = 1_792_764_000_000_000
CHANNEL = "btcusdt@trade"
GOOD_CLOCK = ClockReference(
    [(HOUR + m * MINUTE_US, 100_000) for m in range(0, 61, 5)], window_us=10 * MINUTE_US
)


def mask_for(
    flags: dict[tuple[str, int], set[Flag]],
    gaps: list[GapSpan] | None = None,
    clock: ClockReference = GOOD_CLOCK,
) -> dict[int, tuple[MaskStatus, str]]:
    rows = build_mask(
        hour_start_us=HOUR,
        channels=[CHANNEL],
        flags=flags,
        gaps=gaps or [],
        clock=clock,
        offset_alert_us=250_000,
    )
    assert len(rows) == 60
    return {(r.minute_ts_us - HOUR) // MINUTE_US: (r.status, r.details) for r in rows}


def minute(m: int) -> tuple[str, int]:
    return CHANNEL, HOUR + m * MINUTE_US


def test_minutes_with_data_are_ok_and_others_missing() -> None:
    mask = mask_for({minute(0): {Flag.DATA}})
    assert mask[0] == (MaskStatus.OK, "ok")
    assert mask[1][0] is MaskStatus.MISSING


def test_worst_status_wins_and_details_list_all() -> None:
    mask = mask_for({minute(3): {Flag.DATA, Flag.LATE, Flag.INVALID}})
    assert mask[3] == (MaskStatus.INVALID, "invalid,late")


def test_open_gap_marks_missing_even_with_data() -> None:
    gap = GapSpan(CHANNEL, HOUR + 5 * MINUTE_US + 10, HOUR + 5 * MINUTE_US + 20, "open")
    mask = mask_for({minute(5): {Flag.DATA}, minute(6): {Flag.DATA}}, [gap])
    assert mask[5][0] is MaskStatus.MISSING
    assert mask[6][0] is MaskStatus.OK


def test_recovered_gap_marks_recovered() -> None:
    gap = GapSpan(CHANNEL, HOUR + 7 * MINUTE_US, HOUR + 8 * MINUTE_US, "recovered")
    assert mask_for({minute(7): {Flag.DATA}}, [gap])[7][0] is MaskStatus.RECOVERED


def test_clock_confidence() -> None:
    far_or_bad = ClockReference([(HOUR, 400_000)], window_us=10 * MINUTE_US)
    mask = mask_for({minute(0): {Flag.DATA}, minute(30): {Flag.DATA}}, clock=far_or_bad)
    assert mask[0][0] is MaskStatus.LOW_CLOCK_CONFIDENCE  # offset acima do limite
    assert mask[30][0] is MaskStatus.LOW_CLOCK_CONFIDENCE  # sem medição na janela


def test_clock_reference_picks_nearest_in_window() -> None:
    clock = ClockReference([(100, 1), (200, 2)], window_us=30)
    assert clock.offset_at(120) == 1
    assert clock.offset_at(190) == 2
    assert clock.offset_at(150) is None


def test_worst_defaults_to_ok() -> None:
    assert worst([]) is MaskStatus.OK
