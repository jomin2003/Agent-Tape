"""The Session facade: recording, replaying, and every convenience boundary."""

from __future__ import annotations

import os

import pytest

import agenttape as at
from agenttape.errors import SessionStateError


class ToolTimeout(Exception):
    """A domain exception, defined at module level so replay can re-import it.

    Exceptions raised inside a function body are not reachable as module
    attributes, so replay would fall back to ``RecordedError``.
    """


# --------------------------------------------------------------------------- #
# Round trip
# --------------------------------------------------------------------------- #


def test_replay_reproduces_the_recorded_trajectory(recorded):
    path, agent = recorded
    with at.replay(path) as session:
        assert agent(session) == "Refunds within 30 days."
        assert session.verified


def test_replay_never_invokes_the_wrapped_callables(recorded):
    path, agent = recorded
    calls = []

    def agent_that_would_call_live(session):
        session.tool(
            "search",
            ["refund policy"],
            fn=lambda: calls.append("tool") or ["doc-a", "doc-b"],
        )
        session.model(
            "gpt-4o",
            {"messages": [{"role": "user", "content": ["doc-a", "doc-b"]}]},
            fn=lambda: calls.append("model") or "Refunds within 30 days.",
        )
        session.state({"step": 1}, label="after-model")
        session.outcome({"answer": "Refunds within 30 days."})
        return "Refunds within 30 days."

    with at.replay(path) as session:
        assert agent_that_would_call_live(session) == "Refunds within 30 days."
    assert calls == [], "replay must not execute tools or models"


def test_recording_writes_a_closed_tape(recorded):
    path, _ = recorded
    described = at.Tape.describe(path)
    assert described["closed"] is True
    assert described["digest"]


def test_summary_reports_counts(recorded):
    path, agent = recorded
    with at.replay(path) as session:
        agent(session)
        summary = session.close()
    assert summary["mode"] == "replay"
    assert summary["verified"] is True
    assert summary["remaining"] == 0


def test_recording_summary_reports_counts(tmp_path):
    path = str(tmp_path / "s.tape")
    with at.record(path) as session:
        session.tool("t", [1], fn=lambda: "x")
        session.model("m", {"p": 1}, fn=lambda: "y")
        summary = session.close()
    assert summary["mode"] == "record"
    assert summary["counts"]["tool"] == 1
    assert summary["counts"]["model"] == 1
    assert summary["errors"] == 0


# --------------------------------------------------------------------------- #
# Boundaries
# --------------------------------------------------------------------------- #


def test_exchange_records_and_replays(tape_path):
    with at.record(tape_path) as session:
        value = session.exchange("custom", "thing", {"k": 1}, fn=lambda: "answer")
    assert value == "answer"
    with at.replay(tape_path) as session:
        assert session.exchange("custom", "thing", {"k": 1}, fn=None) == "answer"


def test_exchange_accepts_a_precomputed_response(tape_path):
    with at.record(tape_path) as session:
        assert session.exchange("custom", "thing", {"k": 1}, response="given") == "given"
    with at.replay(tape_path) as session:
        assert session.exchange("custom", "thing", {"k": 1}) == "given"


def test_exchange_requires_an_fn_or_a_response(tape_path):
    with at.record(tape_path) as session:
        with pytest.raises(SessionStateError):
            session.exchange("custom", "thing", {"k": 1})


def test_tool_records_args_and_kwargs_separately(tape_path):
    with at.record(tape_path) as session:
        session.tool("search", ["q"], kwargs={"k": 3}, fn=lambda: "hits")
    event = next(e for e in at.Tape.open(tape_path).iter_events() if e.kind == "tool")
    assert event.request == {"args": ["q"], "kwargs": {"k": 3}}


def test_model_records_the_request_verbatim(tape_path):
    request = {"messages": [{"role": "user", "content": "hi"}], "temperature": 0.0}
    with at.record(tape_path) as session:
        session.model("gpt-4o", request, fn=lambda: "hello")
    event = next(e for e in at.Tape.open(tape_path).iter_events() if e.kind == "model")
    assert event.request == request


def test_wrap_decorator_records_calls(tape_path):
    with at.record(tape_path) as session:

        @session.tool_fn("search")
        def search(query, k=5):
            return ["result for " + query] * k

        assert search("policy") == ["result for policy"] * 5

    with at.replay(tape_path) as session:

        @session.tool_fn("search")
        def search(query, k=5):  # pragma: no cover - must not run
            raise AssertionError("the wrapped function must not be called on replay")

        assert search("policy") == ["result for policy"] * 5


def test_wrap_preserves_function_metadata(tape_path):
    with at.record(tape_path) as session:

        @session.tool_fn("search")
        def search(query):
            """Search the index."""
            return query

        assert search.__name__ == "search"
        assert search.__doc__ == "Search the index."


def test_env_reads_are_recorded(tape_path, monkeypatch):
    monkeypatch.setenv("AGENTTAPE_TEST", "live-value")
    with at.record(tape_path) as session:
        assert session.env("AGENTTAPE_TEST") == "live-value"
    monkeypatch.setenv("AGENTTAPE_TEST", "changed-after-recording")
    with at.replay(tape_path) as session:
        assert session.env("AGENTTAPE_TEST") == "live-value"


def test_env_default_is_used_when_unset(tape_path):
    with at.record(tape_path) as session:
        assert session.env("AGENTTAPE_DEFINITELY_UNSET", "fallback") == "fallback"


def test_read_text_is_recorded(tmp_path):
    source = tmp_path / "doc.txt"
    source.write_text("original contents", encoding="utf-8")
    tape = str(tmp_path / "r.tape")

    with at.record(tape) as session:
        assert session.read_text(source) == "original contents"

    source.write_text("changed after recording", encoding="utf-8")
    with at.replay(tape) as session:
        assert session.read_text(source) == "original contents"


def test_read_bytes_is_recorded(tmp_path):
    source = tmp_path / "blob.bin"
    source.write_bytes(b"\x00\x01\x02")
    tape = str(tmp_path / "r.tape")
    with at.record(tape) as session:
        assert session.read_bytes(source) == b"\x00\x01\x02"
    with at.replay(tape) as session:
        assert session.read_bytes(source) == b"\x00\x01\x02"


def test_uuid4_is_reproduced(tape_path):
    with at.record(tape_path) as session:
        recorded = session.uuid4()
    with at.replay(tape_path) as session:
        assert session.uuid4() == recorded


def test_token_hex_is_reproduced(tape_path):
    with at.record(tape_path) as session:
        recorded = session.token_hex(8)
    assert len(recorded) == 16
    with at.replay(tape_path) as session:
        assert session.token_hex(8) == recorded


# --------------------------------------------------------------------------- #
# State, outcome, annotations
# --------------------------------------------------------------------------- #


def test_state_returns_a_stable_fingerprint(tape_path):
    with at.record(tape_path) as session:
        first = session.state({"a": 1}, label="s")
        second = session.state({"a": 1}, label="s")
    assert first == second


def test_state_fingerprint_changes_with_content(tape_path):
    with at.record(tape_path) as session:
        first = session.state({"a": 1}, label="s")
        second = session.state({"a": 2}, label="s")
    assert first != second


def test_state_fingerprints_are_available_in_replay(tape_path):
    with at.record(tape_path) as session:
        recorded = session.state({"step": 1}, label="checkpoint")
    with at.replay(tape_path) as session:
        assert session.state({"step": 1}, label="checkpoint") == recorded
        assert session.state_fingerprints == [recorded]


def test_outcome_is_recorded_and_readable(tmp_path):
    path = str(tmp_path / "o.tape")
    with at.record(path) as session:
        session.outcome({"answer": "42"})
    with at.replay(path) as session:
        session.outcome({"answer": "42"})
        assert session.outcome_fingerprint is not None
        assert session.outcome_value is None or session.outcome_value == {"answer": "42"}


def test_mark_and_log_are_recorded(tape_path):
    with at.record(tape_path) as session:
        session.mark("checkpoint-a", note="here")
        session.log("something happened")
    counts = at.Tape.describe(tape_path)["counts"]
    assert counts["mark"] == 1
    assert counts["log"] == 1


def test_mark_and_log_replay_positionally(tape_path):
    with at.record(tape_path) as session:
        session.mark("a")
        session.mark("b")
    with at.replay(tape_path) as session:
        session.mark("a")
        session.mark("b")
        assert session.verified


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


def test_a_raised_tool_error_is_recorded_and_re_raised_with_its_type(tape_path):
    def failing():
        raise ToolTimeout("upstream took too long")

    with at.record(tape_path) as session:
        with pytest.raises(ToolTimeout):
            session.tool("slow", [1], fn=failing)

    with at.replay(tape_path) as session:
        with pytest.raises(ToolTimeout) as excinfo:
            session.tool("slow", [1], fn=failing)
        assert "upstream took too long" in str(excinfo.value)


def test_an_unimportable_exception_type_falls_back_to_recorded_error(tape_path):
    """A tape recorded elsewhere may name a class this process does not have."""

    def failing():
        raise RuntimeError("something")

    with at.record(tape_path) as session:
        with pytest.raises(RuntimeError):
            session.tool("slow", [1], fn=failing)

    # Rewrite the recorded error to name a class that does not exist here.
    import json
    from pathlib import Path

    events = Path(tape_path) / "events.jsonl"
    lines = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines()]
    for entry in lines:
        if entry.get("kind") == "tool" and entry.get("error"):
            entry["error"]["type"] = "GhostError"
            entry["error"]["module"] = "nowhere.at.all"
    events.write_text(
        "\n".join(json.dumps(entry, sort_keys=True) for entry in lines) + "\n",
        encoding="utf-8",
    )

    with at.replay(tape_path) as session:
        with pytest.raises(at.RecordedError):
            session.tool("slow", [1], fn=failing)


def test_error_events_are_marked_on_the_tape(tape_path):
    with at.record(tape_path) as session:
        with pytest.raises(ValueError):
            session.tool("bad", [1], fn=lambda: (_ for _ in ()).throw(ValueError("nope")))
    event = next(e for e in at.Tape.open(tape_path).iter_events() if e.kind == "tool")
    assert event.status == "error"
    assert event.error["type"] == "ValueError"


def test_replay_reproduces_control_flow_based_on_the_exception(tape_path):
    """The agent's except-branch must be taken on replay, not the happy path."""

    def agent(session):
        try:
            session.tool("flaky", [1], fn=lambda: (_ for _ in ()).throw(TimeoutError("slow")))
        except TimeoutError:
            return "fell back"
        return "happy path"

    with at.record(tape_path) as session:
        assert agent(session) == "fell back"
    with at.replay(tape_path) as session:
        assert agent(session) == "fell back"


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #


def test_close_is_idempotent(tmp_path):
    path = str(tmp_path / "i.tape")
    session = at.record(path)
    first = session.close()
    second = session.close()
    assert first == second


def test_exchange_after_close_raises(tmp_path):
    path = str(tmp_path / "c.tape")
    session = at.record(path)
    session.close()
    with pytest.raises(SessionStateError):
        session.tool("t", [1], response="x")


def test_session_is_a_context_manager(tmp_path):
    path = str(tmp_path / "cm.tape")
    with at.record(path) as session:
        session.tool("t", [1], response="x")
    assert at.Tape.describe(path)["closed"] is True


def test_context_manager_closes_the_tape_even_on_error(tmp_path):
    path = str(tmp_path / "err.tape")
    with pytest.raises(RuntimeError):
        with at.record(path) as session:
            session.tool("t", [1], response="x")
            raise RuntimeError("agent blew up")
    assert at.Tape.describe(path)["closed"] is True


def test_an_agent_error_is_not_masked_by_a_completion_check(tmp_path):
    """A body exception must win over UnconsumedEventsError."""
    path = str(tmp_path / "mask.tape")
    with at.record(path) as session:
        session.tool("a", [1], response="x")
        session.tool("b", [2], response="y")

    with pytest.raises(RuntimeError, match="the real problem"):
        with at.replay(path) as session:
            session.tool("a", [1])
            raise RuntimeError("the real problem")


def test_record_and_replay_module_helpers(tape_path):
    with at.record(tape_path) as session:
        session.tool("t", [1], response="v")
    with at.replay(tape_path) as session:
        assert session.tool("t", [1]) == "v"


def test_replay_of_a_missing_tape_raises(tmp_path):
    with pytest.raises(at.TapeFormatError):
        at.replay(str(tmp_path / "nope.tape"))


def test_record_refuses_to_clobber_by_default(tape_path):
    at.record(tape_path).close()
    with pytest.raises(at.TapeError):
        at.record(tape_path)


def test_record_can_overwrite_when_asked(tape_path):
    at.record(tape_path).close()
    with at.record(tape_path, overwrite=True) as session:
        session.tool("t", [1], response="fresh")
    assert at.Tape.describe(tape_path)["counts"]["tool"] == 1


def test_header_records_the_environment(tape_path):
    with at.record(tape_path) as session:
        session.tool("t", [1], response="v")
    header = next(e for e in at.Tape.open(tape_path).iter_events() if e.kind == "record")
    assert header.response["agenttape_version"] == at.__version__
    assert header.response["python"]
    assert "cwd" in header.response


def test_repr_is_informative(tape_path):
    with at.record(tape_path) as session:
        assert "record" in repr(session)
        assert os.path.basename(tape_path) in repr(session)
