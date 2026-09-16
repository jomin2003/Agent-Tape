"""Integrity verification, tape diffing, and the determinism self-test."""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

import agenttape as at
from agenttape.tape import BLOBS_DIR, EVENTS_NAME, MANIFEST_NAME


@pytest.fixture
def tape(tmp_path):
    path = str(tmp_path / "v.tape")
    with at.record(path) as session:
        session.tool("search", ["alpha"], fn=lambda: "hits")
        session.model("gpt-4o", {"prompt": "one"}, fn=lambda: "answer")
        session.state({"step": 1}, label="checkpoint")
        session.outcome({"answer": "answer"})
    return path


# --------------------------------------------------------------------------- #
# verify
# --------------------------------------------------------------------------- #


def test_a_freshly_recorded_tape_verifies(tape):
    report = at.verify(tape)
    assert report.ok
    assert report.chain_ok
    assert report.digest_matches
    assert report.closed
    assert report.problems == []


def test_report_counts_events_by_kind(tape):
    report = at.verify(tape)
    assert report.counts["tool"] == 1
    assert report.counts["model"] == 1
    assert report.counts["state"] == 1
    assert report.counts["outcome"] == 1
    assert "footer" not in report.counts


def test_report_records_the_format_and_seed(tape):
    report = at.verify(tape)
    assert report.format_version == at.TAPE_FORMAT_VERSION
    assert report.seed is not None
    assert report.agenttape_version == at.__version__


def test_editing_an_event_breaks_the_chain(tape):
    events_path = Path(tape) / EVENTS_NAME
    lines = events_path.read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[1])
    payload["response"] = "tampered"
    lines[1] = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = at.verify(tape)
    assert not report.ok
    assert not report.chain_ok
    assert report.first_bad_seq is not None
    assert any("hash mismatch" in problem for problem in report.problems)


def test_reordering_events_breaks_the_chain(tape):
    events_path = Path(tape) / EVENTS_NAME
    lines = events_path.read_text(encoding="utf-8").splitlines()
    lines[1], lines[2] = lines[2], lines[1]
    events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = at.verify(tape)
    assert not report.ok
    assert not report.chain_ok


def test_a_stale_manifest_digest_is_reported(tape):
    manifest_path = Path(tape) / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["digest"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    report = at.verify(tape)
    assert not report.ok
    assert report.chain_ok  # the events themselves are fine
    assert not report.digest_matches


def test_digest_check_can_be_skipped(tape):
    manifest_path = Path(tape) / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["digest"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert at.verify(tape, check_digest=False).ok


def test_missing_blobs_are_reported(tmp_path):
    path = str(tmp_path / "blobby.tape")
    with at.record(path, blob_threshold=128) as session:
        session.tool("fetch", ["big"], fn=lambda: "x" * 4000)

    for blob in (tmp_path / "blobby.tape" / BLOBS_DIR).iterdir():
        blob.unlink()

    report = at.verify(path)
    assert not report.ok
    assert report.blobs_checked == 1
    assert not report.blobs_ok
    assert report.missing_blobs


def test_blob_checks_can_be_skipped(tmp_path):
    path = str(tmp_path / "blobby.tape")
    with at.record(path, blob_threshold=128) as session:
        session.tool("fetch", ["big"], fn=lambda: "x" * 4000)
    for blob in (tmp_path / "blobby.tape" / BLOBS_DIR).iterdir():
        blob.unlink()

    assert at.verify(path, check_blobs=False).ok


def test_integrity_and_payload_availability_fail_independently(tmp_path):
    """A tape with a damaged blob store still verifies, and still cannot be read.

    This separation is the whole reason the event hash covers the body *as
    stored* rather than the logical one. If the hash covered logical content,
    verifying a tape would require resolving its blobs, and "someone edited this
    tape" would be indistinguishable from "a blob file is missing" -- two very
    different problems with two very different responses.
    """
    path = str(tmp_path / "blobby.tape")
    with at.record(path, blob_threshold=128) as session:
        session.tool("fetch", ["big"], fn=lambda: "x" * 4000)

    for blob in (tmp_path / "blobby.tape" / BLOBS_DIR).iterdir():
        blob.unlink()

    # Tamper detection still works, because it never touches the blob store.
    report = at.verify(path, check_blobs=False)
    assert report.chain_ok
    assert report.digest_matches
    assert report.ok

    # And the payload genuinely is gone.
    with pytest.raises(at.TapeIntegrityError):
        list(at.Tape.open(path).iter_events())

    # With blob checking on, the missing payload is reported as its own problem.
    report = at.verify(path)
    assert not report.ok
    assert report.chain_ok  # the log itself is untouched
    assert report.missing_blobs


def test_report_serialises_and_renders(tape):
    report = at.verify(tape)
    assert json.dumps(report.to_dict())
    rendered = report.render()
    assert "OK" in rendered
    assert "chain" in rendered


def test_report_render_shows_problems(tape):
    events_path = Path(tape) / EVENTS_NAME
    lines = events_path.read_text(encoding="utf-8").splitlines()
    lines[1] = lines[1].replace("hits", "tampered")
    events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert "FAILED" in at.verify(tape).render()


def test_verify_rejects_a_non_tape(tmp_path):
    (tmp_path / "nothing").mkdir()
    with pytest.raises(at.TapeFormatError):
        at.verify(tmp_path / "nothing")


# --------------------------------------------------------------------------- #
# diff
# --------------------------------------------------------------------------- #


def test_identical_tapes_diff_clean(tape, tmp_path):
    copy = str(tmp_path / "copy.tape")
    at.Tape.fork(tape, copy).close()
    result = at.diff(tape, copy)
    assert result.identical
    assert result.common_events > 0


def test_diff_reports_the_first_difference(tmp_path):
    first = str(tmp_path / "a.tape")
    second = str(tmp_path / "b.tape")

    with at.record(first) as session:
        session.tool("a", [1], fn=lambda: "one")
        session.tool("b", [2], fn=lambda: "two")
        session.tool("c", [3], fn=lambda: "three")

    with at.record(second) as session:
        session.tool("a", [1], fn=lambda: "one")
        session.tool("b", [999], fn=lambda: "two")
        session.tool("c", [3], fn=lambda: "three")

    result = at.diff(first, second)
    assert not result.identical
    assert result.first_difference_seq == 1
    assert result.expected["name"] == "b"
    assert "999" in result.detail
    assert "recorded" in result.render()


def test_diff_reports_length_mismatches(tmp_path):
    short = str(tmp_path / "short.tape")
    long = str(tmp_path / "long.tape")
    with at.record(short) as session:
        session.tool("a", [1], fn=lambda: "one")
    with at.record(long) as session:
        session.tool("a", [1], fn=lambda: "one")
        session.tool("b", [2], fn=lambda: "two")

    forward = at.diff(short, long)
    assert forward.reason == "the second tape is longer"
    backward = at.diff(long, short)
    assert backward.reason == "the first tape is longer"


def test_diff_strict_mode_also_compares_responses(tmp_path):
    first = str(tmp_path / "a.tape")
    second = str(tmp_path / "b.tape")
    with at.record(first) as session:
        session.tool("a", [1], response="same-request")
    with at.record(second) as session:
        session.tool("a", [1], response="different-response")

    assert at.diff(first, second).identical
    strict = at.diff(first, second, strict=True)
    assert not strict.identical
    assert "different recorded response" in strict.reason


def test_diff_serialises(tape, tmp_path):
    copy = str(tmp_path / "copy.tape")
    at.Tape.fork(tape, copy).close()
    assert json.dumps(at.diff(tape, copy).to_dict())


# --------------------------------------------------------------------------- #
# check_determinism
# --------------------------------------------------------------------------- #


def test_a_fully_wrapped_agent_is_deterministic(tape):
    def agent(session):
        session.tool("search", ["alpha"])
        session.model("gpt-4o", {"prompt": "one"})
        session.state({"step": 1}, label="checkpoint")
        session.outcome({"answer": "answer"})
        return "answer"

    report = at.check_determinism(tape, agent, repeats=3)
    assert report.deterministic, report.problems
    assert len(report.runs) == 3
    assert all(run["remaining"] == 0 for run in report.runs)
    assert len({run["outcome_fingerprint"] for run in report.runs}) == 1


def test_determinism_check_detects_unwrapped_randomness(tmp_path):
    """An agent reading raw entropy is not reproducible, and the check says so."""
    path = str(tmp_path / "leaky.tape")

    def leaky(session):
        session.outcome({"roll": random.random()})

    with at.record(path) as session:
        leaky(session)

    report = at.check_determinism(path, leaky, repeats=3)
    assert not report.deterministic
    assert report.problems


def test_determinism_check_detects_a_shorter_replay(tmp_path):
    path = str(tmp_path / "s.tape")
    with at.record(path) as session:
        session.tool("a", [1], fn=lambda: "one")
        session.tool("b", [2], fn=lambda: "two")

    def truncated(session):
        session.tool("a", [1])

    report = at.check_determinism(path, truncated, repeats=2)
    assert not report.deterministic
    assert any("unconsumed" in problem for problem in report.problems)


def test_determinism_check_reports_an_agent_exception(tmp_path):
    path = str(tmp_path / "e.tape")
    with at.record(path) as session:
        session.tool("a", [1], fn=lambda: "one")

    def broken(session):
        raise RuntimeError("agent is broken")

    report = at.check_determinism(path, broken, repeats=2)
    assert not report.deterministic
    assert any("agent is broken" in problem for problem in report.problems)


def test_determinism_report_serialises_and_renders(tape):
    def agent(session):
        session.tool("search", ["alpha"])
        session.model("gpt-4o", {"prompt": "one"})
        session.state({"step": 1}, label="checkpoint")
        session.outcome({"answer": "answer"})

    report = at.check_determinism(tape, agent, repeats=2)
    assert json.dumps(report.to_dict())
    assert "DETERMINISTIC" in report.render()


def test_determinism_check_replays_the_clock_and_rng(tmp_path):
    path = str(tmp_path / "cr.tape")

    def agent(session):
        session.clock.time()
        session.rng.random()
        session.outcome({"ok": True})

    with at.record(path) as session:
        agent(session)

    assert at.check_determinism(path, agent, repeats=3).deterministic
