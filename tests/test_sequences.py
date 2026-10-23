import pytest

from trader.core.timeunits import MINUTE_US, SECOND_US
from trader.quality.sequences import (
    DiffContinuityTracker,
    IdGap,
    MinuteSequenceTracker,
    SeqStatus,
    SilenceMonitor,
    TradeIdTracker,
    missing_minutes,
)

T0 = 1_792_779_000_000_000


class TestTradeIds:
    def test_consecutive_ids(self) -> None:
        tracker = TradeIdTracker()
        assert [tracker.observe(i).status for i in (10, 11, 12)] == [SeqStatus.OK] * 3

    def test_gap_reports_missing_range(self) -> None:
        tracker = TradeIdTracker(last_id=10)
        result = tracker.observe(15)
        assert result.status is SeqStatus.GAP
        assert result.gap == IdGap(11, 14)
        assert result.gap.size == 4
        assert tracker.observe(16).status is SeqStatus.OK

    def test_repeated_ids_from_rotation_overlap(self) -> None:
        tracker = TradeIdTracker(last_id=20)
        assert tracker.observe(20).status is SeqStatus.REPEATED
        assert tracker.observe(18).status is SeqStatus.REPEATED
        assert tracker.last_id == 20


class TestDiffContinuity:
    def test_continuous_and_overlapping_updates(self) -> None:
        tracker = DiffContinuityTracker()
        assert tracker.observe(100, 104).status is SeqStatus.OK
        assert tracker.observe(105, 110).status is SeqStatus.OK
        assert tracker.observe(108, 112).status is SeqStatus.OK

    def test_break(self) -> None:
        tracker = DiffContinuityTracker(last_final_id=110)
        result = tracker.observe(115, 120)
        assert result.status is SeqStatus.GAP
        assert result.gap == IdGap(111, 114)

    def test_old_message_is_repeated(self) -> None:
        tracker = DiffContinuityTracker(last_final_id=110)
        assert tracker.observe(100, 110).status is SeqStatus.REPEATED


class TestMinutes:
    def test_live_tracker_reports_missing_minutes(self) -> None:
        tracker = MinuteSequenceTracker()
        assert tracker.observe(T0) == []
        assert tracker.observe(T0 + MINUTE_US) == []
        assert tracker.observe(T0 + 4 * MINUTE_US) == [T0 + 2 * MINUTE_US, T0 + 3 * MINUTE_US]
        assert tracker.observe(T0) == []

    def test_misaligned_open_time_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            MinuteSequenceTracker().observe(T0 + 1)

    def test_batch_audit(self) -> None:
        present = [T0, T0 + 2 * MINUTE_US]
        assert missing_minutes(present, T0, T0 + 3 * MINUTE_US) == [T0 + MINUTE_US]
        assert missing_minutes(present, T0 + 1, T0 + 3 * MINUTE_US) == [T0 + MINUTE_US]


class TestSilence:
    def test_channel_becomes_silent_after_limit(self) -> None:
        monitor = SilenceMonitor(
            {"depth": 2 * SECOND_US, "trade": 30 * SECOND_US}, started_mono_us=0
        )
        monitor.touch("depth", 1 * SECOND_US)
        assert monitor.silent(3 * SECOND_US) == []
        assert monitor.silent(3 * SECOND_US + 1) == ["depth"]
        assert monitor.age_us("trade", 3 * SECOND_US) == 3 * SECOND_US

    def test_unknown_channels_are_ignored(self) -> None:
        monitor = SilenceMonitor({"depth": SECOND_US}, started_mono_us=0)
        monitor.touch("other", 10)
        assert monitor.silent(SECOND_US) == []
