"""Divergence detection: the property that makes replay trustworthy.

A replay engine that guesses is worse than no replay engine, because it
manufactures false confidence. These tests pin down the "never guess" contract.
"""

from __future__ import annotations

import pytest

import agenttape as at
from agenttape.errors import (
    DivergenceError,
    RequestMismatchError,
    TapeExhaustedError,
    UnconsumedEventsError,
)


@pytest.fixture
def simple_tape(tmp_path):
    """A tape with three distinct calls."""
    path = str(tmp_path / "simple.tape")
    with at.record(path) as session:
        session.tool("search", ["alpha"], fn=lambda: "hits-alpha")
        session.model("gpt-4o", {"prompt": "one"}, fn=lambda: "answer-one")
        session.tool("write", ["file.txt"], fn=lambda: "written")
    return path


# --------------------------------------------------------------------------- #
# Changing a request
# --------------------------------------------------------------------------- #


def test_changing_a_tool_argument_is_detected(simple_tape):
    with pytest.raises(RequestMismatchError) as excinfo:
        with at.replay(simple_tape) as session:
            session.tool("search", ["BETA"], fn=lambda: "hits-alpha")
    assert excinfo.value.seq == 1


def test_changing_a_prompt_is_detected(simple_tape):
    with pytest.raises(RequestMismatchError):
        with at.replay(simple_tape) as session:
            session.tool("search", ["alpha"], fn=lambda: "x")
            session.model("gpt-4o", {"prompt": "TWO"}, fn=lambda: "y")


def test_changing_a_sampling_parameter_is_detected(simple_tape):
    with pytest.raises(RequestMismatchError):
        with at.replay(simple_tape) as session:
            session.tool("search", ["alpha"], fn=lambda: "x")
            session.model("gpt-4o", {"prompt": "one", "temperature": 0.7}, fn=lambda: "y")


def test_swapping_two_calls_of_the_same_name_is_detected(tmp_path):
    path = str(tmp_path / "swap.tape")
    with at.record(path) as session:
        session.tool("search", ["a"], fn=lambda: "A")
        session.tool("search", ["b"], fn=lambda: "B")

    with pytest.raises(RequestMismatchError):
        with at.replay(path) as session:
            session.tool("search", ["b"], fn=lambda: "B")
            session.tool("search", ["a"], fn=lambda: "A")


def test_calling_a_different_tool_is_detected(simple_tape):
    with pytest.raises(RequestMismatchError) as excinfo:
        with at.replay(simple_tape) as session:
            session.tool("delete", ["everything"], fn=lambda: "oops")
    assert "different kind/name" in excinfo.value.detail


def test_using_a_different_boundary_kind_is_detected(simple_tape):
    with pytest.raises(RequestMismatchError):
        with at.replay(simple_tape) as session:
            session.model("search", {"args": ["alpha"], "kwargs": {}}, fn=lambda: "x")


def test_a_key_that_disagrees_with_its_request_is_reported_clearly(tmp_path):
    """A tape whose stored key does not match its stored request.

    Hand-written tapes and other implementations can produce this. The request
    payloads then diff as identical, which is confusing unless the message says
    why -- so the diff falls back to naming the key as the difference.
    """
    from agenttape import EventKind

    path = str(tmp_path / "odd.tape")
    # Exactly what session.tool("search", ["alpha"]) builds, so the two payloads
    # really are identical and the only thing that can differ is the key.
    request = {"args": ["alpha"], "kwargs": {}}
    with at.Tape.create(path) as tape:
        tape.append(
            kind=EventKind.TOOL,
            name="search",
            key="deliberately-not-the-fingerprint-of-the-request",
            request=request,
            response="hits",
        )

    with pytest.raises(RequestMismatchError) as excinfo:
        with at.replay(path) as session:
            session.tool("search", ["alpha"], fn=lambda: "hits")

    assert "structurally identical" in excinfo.value.detail
    assert "key" in excinfo.value.detail


def test_a_large_diff_is_truncated(tmp_path):
    """A divergence on a huge payload must not print the whole thing."""
    path = str(tmp_path / "big.tape")
    big = {"field_{:03d}".format(i): "value-{}".format(i) for i in range(200)}
    # Change enough fields that the unified diff runs past the 40-line cap.
    changed = dict(big)
    for i in range(60):
        changed["field_{:03d}".format(i)] = "CHANGED"

    with at.record(path) as session:
        session.tool("search", [big], fn=lambda: "hits")

    with pytest.raises(RequestMismatchError) as excinfo:
        with at.replay(path) as session:
            session.tool("search", [changed], fn=lambda: "hits")

    assert "diff truncated" in excinfo.value.detail


# --------------------------------------------------------------------------- #
# Run length
# --------------------------------------------------------------------------- #


def test_a_longer_run_is_detected(simple_tape):
    with pytest.raises(TapeExhaustedError) as excinfo:
        with at.replay(simple_tape) as session:
            session.tool("search", ["alpha"])
            session.model("gpt-4o", {"prompt": "one"})
            session.tool("write", ["file.txt"])
            session.tool("extra", ["step"])
    assert excinfo.value.kind == "tape_exhausted"


def test_a_shorter_run_is_detected(simple_tape):
    with pytest.raises(UnconsumedEventsError) as excinfo:
        with at.replay(simple_tape) as session:
            session.tool("search", ["alpha"])
    assert "shorter than the recorded run" in str(excinfo.value)
    assert len(excinfo.value.remaining) == 2


def test_a_shorter_run_can_be_allowed_explicitly(simple_tape):
    session = at.replay(simple_tape, require_full_consumption=False)
    session.tool("search", ["alpha"])
    summary = session.close()
    assert summary["remaining"] == 2


# --------------------------------------------------------------------------- #
# Policies
# --------------------------------------------------------------------------- #


def test_warn_policy_records_the_divergence_and_continues_when_it_can(tmp_path):
    path = str(tmp_path / "warn.tape")
    with at.record(path) as session:
        session.tool("a", [1], fn=lambda: "one")
        session.tool("b", [2], fn=lambda: "two")

    session = at.replay(path, strict=False, on_divergence="warn")
    assert session.tool("b", [2]) == "two"
    assert session.tool("a", [1]) == "one"
    # Two ordering divergences: the first call jumped ahead of the recording,
    # and the second consumed the event that had been passed over.
    assert len(session.divergences) == 2
    assert all(record["recovered"] for record in session.divergences)
    assert session.divergences[0]["expected"]["name"] == "a"
    session.close()


def test_warn_policy_still_raises_when_the_call_is_absent(tmp_path):
    path = str(tmp_path / "warn2.tape")
    with at.record(path) as session:
        session.tool("a", [1], fn=lambda: "one")

    with pytest.raises(TapeExhaustedError):
        with at.replay(path, strict=False, on_divergence="warn") as session:
            session.tool("never-recorded", [9])


def test_non_strict_mode_finds_a_later_matching_event(tmp_path):
    path = str(tmp_path / "lenient.tape")
    with at.record(path) as session:
        session.tool("a", [1], fn=lambda: "one")
        session.tool("b", [2], fn=lambda: "two")

    with at.replay(path, strict=False) as session:
        # Ask for b first; the engine searches forward and finds it.
        assert session.tool("b", [2]) == "two"
        assert session.tool("a", [1]) == "one"


def test_strict_mode_does_not_search_forward(tmp_path):
    path = str(tmp_path / "strict.tape")
    with at.record(path) as session:
        session.tool("a", [1], fn=lambda: "one")
        session.tool("b", [2], fn=lambda: "two")

    with pytest.raises(RequestMismatchError):
        with at.replay(path, strict=True) as session:
            session.tool("b", [2])


def test_lookahead_bounds_the_search(tmp_path):
    path = str(tmp_path / "lookahead.tape")
    with at.record(path) as session:
        for index in range(10):
            session.tool("t", [index], fn=lambda index=index: index)

    with pytest.raises(TapeExhaustedError):
        with at.replay(path, strict=False, lookahead=2) as session:
            session.tool("t", [9])


def test_invalid_on_divergence_value_is_rejected(tape_path):
    at.record(tape_path).close()
    with pytest.raises(ValueError):
        at.replay(tape_path, on_divergence="explode")


# --------------------------------------------------------------------------- #
# Error surface
# --------------------------------------------------------------------------- #


def test_every_replay_failure_is_a_divergence_error():
    for error in (RequestMismatchError, TapeExhaustedError, UnconsumedEventsError):
        assert issubclass(error, DivergenceError)
    assert issubclass(DivergenceError, at.AgentTapeError)


def test_divergence_error_serialises_for_reports(simple_tape):
    try:
        with at.replay(simple_tape) as session:
            session.tool("search", ["different"])
    except DivergenceError as exc:
        payload = exc.as_dict()
    assert payload["kind"] == "request_mismatch"
    assert payload["seq"] == 1
    assert payload["expected"]["name"] == "search"
    assert payload["observed"]["name"] == "search"


def test_divergence_error_carries_a_readable_diff(simple_tape):
    try:
        with at.replay(simple_tape) as session:
            session.tool("search", ["different"])
    except DivergenceError as exc:
        detail = exc.detail
    assert "recorded" in detail
    assert "replayed" in detail
    assert "alpha" in detail
    assert "different" in detail


def test_verified_is_false_when_a_divergence_was_tolerated(tmp_path):
    path = str(tmp_path / "v.tape")
    with at.record(path) as session:
        session.tool("a", [1], fn=lambda: "one")
        session.tool("b", [2], fn=lambda: "two")

    session = at.replay(path, strict=False, on_divergence="warn")
    session.tool("b", [2])
    assert session.verified is False
    session.tool("a", [1])
    session.close()


def test_divergence_does_not_leak_state_into_the_next_session(simple_tape):
    with pytest.raises(RequestMismatchError):
        with at.replay(simple_tape) as session:
            session.tool("search", ["different"])

    with at.replay(simple_tape) as session:
        assert session.tool("search", ["alpha"]) == "hits-alpha"
        assert session.model("gpt-4o", {"prompt": "one"}) == "answer-one"
        assert session.tool("write", ["file.txt"]) == "written"
        assert session.verified
