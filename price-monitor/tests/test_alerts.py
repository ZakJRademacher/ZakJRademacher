from price_monitor.alerts import detect_changes
from price_monitor.store import Observation


def prev(cents, stock=True):
    return Observation("2026-01-01T00:00:00+00:00", cents, stock)


def kinds(changes):
    return [c.kind for c in changes]


def test_first_observation_has_no_changes():
    assert detect_changes(None, 1000, True, None, 1.0) == []


def test_price_drop_over_threshold():
    (change,) = detect_changes(prev(2000), 1700, True, None, 1.0)
    assert (change.kind, change.old_cents, change.new_cents, change.pct_change) == ("price_drop", 2000, 1700, -15.0)


def test_small_drop_below_threshold_is_ignored():
    assert detect_changes(prev(10000), 9950, True, None, 1.0) == []


def test_price_rise():
    assert kinds(detect_changes(prev(1000), 1100, True, None, 1.0)) == ["price_rise"]


def test_stock_transitions():
    assert kinds(detect_changes(prev(1000, False), 1000, True, None, 1.0)) == ["back_in_stock"]
    assert kinds(detect_changes(prev(1000, True), 1000, False, None, 1.0)) == ["out_of_stock"]
    assert detect_changes(prev(1000, None), 1000, False, None, 1.0) == []


def test_target_hit_fires_once():
    assert "target_hit" in kinds(detect_changes(prev(6000), 4900, True, 5000, 1.0))
    assert "target_hit" not in kinds(detect_changes(prev(4900), 4800, True, 5000, 1.0))
    assert kinds(detect_changes(None, 4000, True, 5000, 1.0)) == ["target_hit"]
