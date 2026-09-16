"""Counterfactual replay: forking a recording and continuing live."""

from __future__ import annotations

import pytest

import agenttape as at
from agenttape.errors import TapeExhaustedError, TapeError


@pytest.fixture
def short_tape(tmp_path):
    """A recording of a two-step run."""
    path = str(tmp_path / "short.tape")
    with at.record(path) as session:
        session.tool("step", [1], fn=lambda: "one")
        session.tool("step", [2], fn=lambda: "two")
    return path


def test_going_live_requires_a_fork_destination(short_tape):
    with pytest.raises(ValueError, match="fork_to"):
        at.replay(short_tape, on_exhausted="live")


def test_invalid_on_exhausted_value_is_rejected(short_tape):
    with pytest.raises(ValueError):
        at.replay(short_tape, on_exhausted="whatever")


def test_replay_raises_by_default_when_the_recording_runs_out(short_tape):
    with pytest.raises(TapeExhaustedError):
        with at.replay(short_tape) as session:
            session.tool("step", [1])
            session.tool("step", [2])
            session.tool("step", [3])


def test_live_tail_executes_only_the_new_steps(short_tape, tmp_path):
    fork = str(tmp_path / "fork.tape")
    executed = []

    def agent(session):
        session.tool("step", [1], fn=lambda: executed.append(1) or "one")
        session.tool("step", [2], fn=lambda: executed.append(2) or "two")
        return session.tool("step", [3], fn=lambda: executed.append(3) or "three")

    with at.replay(short_tape, on_exhausted="live", fork_to=fork) as session:
        assert agent(session) == "three"
        assert session.forked is True
        assert session.fork_seq == 2
        assert session.mode == "record"

    assert executed == [3], "the recorded steps must not have been re-executed"


def test_live_tail_leaves_the_original_recording_intact(short_tape, tmp_path):
    before = at.verify(short_tape)
    fork = str(tmp_path / "fork.tape")

    with at.replay(short_tape, on_exhausted="live", fork_to=fork) as session:
        session.tool("step", [1])
        session.tool("step", [2])
        session.tool("step", [3], fn=lambda: "three")

    after = at.verify(short_tape)
    assert before.digest == after.digest
    assert after.events == before.events


def test_the_fork_records_its_lineage_and_verifies(short_tape, tmp_path):
    fork = str(tmp_path / "fork.tape")
    with at.replay(short_tape, on_exhausted="live", fork_to=fork) as session:
        session.tool("step", [1])
        session.tool("step", [2])
        session.tool("step", [3], fn=lambda: "three")

    manifest = at.Tape.describe(fork)
    assert manifest["parent"]["path"] == short_tape
    assert manifest["closed"] is True

    report = at.verify(fork)
    assert report.ok, report.problems
    # Header + two copied calls + the fork mark + the live step + the footer.
    assert report.events == 6
    assert report.events == manifest["event_count"]
    assert report.counts["tool"] == 3


def test_the_fork_replays_like_any_other_tape(short_tape, tmp_path):
    fork = str(tmp_path / "fork.tape")

    def agent(session):
        session.tool("step", [1])
        session.tool("step", [2])
        session.tool("step", [3], fn=lambda: "three")

    with at.replay(short_tape, on_exhausted="live", fork_to=fork) as session:
        agent(session)

    with at.replay(fork) as session:
        agent(session)
        assert session.verified


def test_fork_destination_must_not_exist(short_tape, tmp_path):
    fork = str(tmp_path / "fork.tape")
    at.Tape.fork(short_tape, fork).close()
    with pytest.raises(TapeError):
        at.replay(short_tape, on_exhausted="live", fork_to=fork)


def test_a_forked_session_is_never_reported_as_verified(short_tape, tmp_path):
    fork = str(tmp_path / "fork.tape")
    with at.replay(short_tape, on_exhausted="live", fork_to=fork) as session:
        session.tool("step", [1])
        session.tool("step", [2])
        assert session.forked is False
        assert session.verified is True

        session.tool("step", [3], fn=lambda: "three")
        assert session.forked is True
        assert session.verified is False


def test_diff_between_a_recording_and_its_counterfactual(tmp_path):
    """The counterfactual differs from the original exactly where it branched."""
    original = str(tmp_path / "original.tape")
    fork = str(tmp_path / "fork.tape")

    def agent(session):
        session.tool("step", [1], fn=lambda: "one")
        session.tool("step", [2], fn=lambda: "two")

    with at.record(original) as session:
        agent(session)

    def extended(session):
        session.tool("step", [1], fn=lambda: "one")
        session.tool("step", [2], fn=lambda: "two")
        session.tool("step", [3], fn=lambda: "three")

    with at.replay(original, on_exhausted="live", fork_to=fork) as session:
        extended(session)

    result = at.diff(original, fork)
    assert not result.identical
    assert result.reason == "the second tape is longer"


def test_diff_detects_a_changed_step(short_tape, tmp_path):
    other = str(tmp_path / "other.tape")
    with at.record(other) as session:
        session.tool("step", [1], fn=lambda: "one")
        session.tool("step", [99], fn=lambda: "different")

    result = at.diff(short_tape, other)
    assert not result.identical
    assert result.first_difference_seq == 1
    assert result.reason == "different call"
    assert "99" in result.detail
