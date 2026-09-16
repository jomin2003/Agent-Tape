"""The recorded clock."""

from __future__ import annotations

import datetime as dt
import time

import agenttape as at


def test_time_is_recorded_and_reproduced(tape_path):
    with at.record(tape_path) as session:
        recorded = session.clock.time()
    assert recorded == int(recorded) or isinstance(recorded, float)
    with at.replay(tape_path) as session:
        assert session.clock.time() == recorded


def test_now_is_recorded_and_reproduced(tape_path):
    with at.record(tape_path) as session:
        recorded = session.clock.now()
    with at.replay(tape_path) as session:
        assert session.clock.now() == recorded


def test_now_keeps_its_utc_offset(tape_path):
    tz = dt.timezone(dt.timedelta(hours=5, minutes=30))
    with at.record(tape_path) as session:
        recorded = session.clock.now(tz)
    assert recorded.utcoffset() == dt.timedelta(hours=5, minutes=30)
    with at.replay(tape_path) as session:
        assert session.clock.now(tz) == recorded


def test_utcnow_is_timezone_aware_and_reproduced(tape_path):
    with at.record(tape_path) as session:
        recorded = session.clock.utcnow()
    assert recorded.tzinfo is not None
    with at.replay(tape_path) as session:
        assert session.clock.utcnow() == recorded


def test_today_is_reproduced(tape_path):
    with at.record(tape_path) as session:
        recorded = session.clock.today()
    with at.replay(tape_path) as session:
        assert session.clock.today() == recorded


def test_monotonic_is_reproduced(tape_path):
    with at.record(tape_path) as session:
        first = session.clock.monotonic()
        second = session.clock.monotonic()
    assert second >= first
    with at.replay(tape_path) as session:
        assert session.clock.monotonic() == first
        assert session.clock.monotonic() == second


def test_sleep_actually_sleeps_while_recording(tape_path):
    with at.record(tape_path) as session:
        started = time.perf_counter()
        session.clock.sleep(0.05)
        elapsed = time.perf_counter() - started
    assert elapsed >= 0.04


def test_sleep_returns_immediately_while_replaying(tape_path):
    with at.record(tape_path) as session:
        session.clock.sleep(0.3)
    with at.replay(tape_path) as session:
        started = time.perf_counter()
        session.clock.sleep(0.3)
        elapsed = time.perf_counter() - started
    assert elapsed < 0.1, "replay should not wait on recorded backoff timers"


def test_isoformat_is_reproduced(tape_path):
    with at.record(tape_path) as session:
        recorded = session.clock.isoformat()
    with at.replay(tape_path) as session:
        assert session.clock.isoformat() == recorded


def test_clock_is_callable(tape_path):
    with at.record(tape_path) as session:
        recorded = session.clock()
    with at.replay(tape_path) as session:
        assert session.clock() == recorded


def test_clock_reads_are_recorded_as_clock_events(tape_path):
    with at.record(tape_path) as session:
        session.clock.time()
        session.clock.monotonic()
    counts = at.Tape.describe(tape_path)["counts"]
    assert counts["clock"] == 2


def test_differing_clock_calls_are_matched_positionally(tape_path):
    """Two time() calls must be answered by the two recorded readings, in order."""
    with at.record(tape_path) as session:
        first = session.clock.time()
        time.sleep(0.01)
        second = session.clock.time()
    assert first != second
    with at.replay(tape_path) as session:
        assert session.clock.time() == first
        assert session.clock.time() == second
