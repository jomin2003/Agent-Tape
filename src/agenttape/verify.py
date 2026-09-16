"""Verification, comparison, and determinism checking.

Three questions this module answers:

``verify(path)``
    *Is this tape intact?* Walks the hash chain, compares the computed head
    against the manifest digest, checks every referenced blob against its
    content address, and reports counts. This is the "has anyone edited the
    recording?" check.

``diff(a, b)``
    *Where did these two runs first differ?* Compares two tapes event by event
    and reports the first divergence with a structural diff. This is the
    regression-testing primitive: record a golden run, replay against new code,
    diff the tapes.

``check_determinism(path, run)``
    *Does this recording actually replay?* Re-runs the agent against the tape
    several times and confirms every pass consumes the whole tape and lands on
    the same outcome and the same state fingerprints. A tape that fails this is
    not a usable recording -- there is a boundary the library is not seeing.

All three return plain dataclasses with a :meth:`~TapeReport.to_dict` method and
a :meth:`~TapeReport.render` string, so results are equally usable from a test
assertion, a JSON report, or a terminal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .canonical import TAG, fingerprint, short_hash
from .channel import request_diff
from .errors import AgentTapeError, DivergenceError, TapeIntegrityError
from .events import GENESIS, EventKind, event_hash
from .session import Session
from .tape import Tape

__all__ = [
    "TapeReport",
    "TapeDiff",
    "DeterminismReport",
    "verify",
    "diff",
    "check_determinism",
]


# --------------------------------------------------------------------------- #
# verify
# --------------------------------------------------------------------------- #


@dataclass
class TapeReport:
    """Result of :func:`verify`."""

    path: str
    ok: bool = False
    format_version: int = 0
    closed: bool = False
    events: int = 0
    counts: Dict[str, int] = field(default_factory=dict)
    digest: str = ""
    manifest_digest: str = ""
    digest_matches: bool = False
    chain_ok: bool = False
    first_bad_seq: Optional[int] = None
    blobs_checked: int = 0
    blobs_ok: bool = True
    missing_blobs: List[str] = field(default_factory=list)
    parent: Optional[Dict[str, Any]] = None
    seed: Optional[int] = None
    created_at: Optional[str] = None
    agenttape_version: Optional[str] = None
    problems: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Plain-dict form, suitable for JSON output."""
        return {
            "path": self.path,
            "ok": self.ok,
            "format_version": self.format_version,
            "closed": self.closed,
            "events": self.events,
            "counts": self.counts,
            "digest": self.digest,
            "manifest_digest": self.manifest_digest,
            "digest_matches": self.digest_matches,
            "chain_ok": self.chain_ok,
            "first_bad_seq": self.first_bad_seq,
            "blobs_checked": self.blobs_checked,
            "blobs_ok": self.blobs_ok,
            "missing_blobs": self.missing_blobs,
            "parent": self.parent,
            "seed": self.seed,
            "created_at": self.created_at,
            "agenttape_version": self.agenttape_version,
            "problems": self.problems,
        }

    def render(self) -> str:
        """Human-readable multi-line summary."""
        status = "OK" if self.ok else "FAILED"
        lines = [
            "tape {}  [{}]".format(self.path, status),
            "  events         {}".format(self.events),
            "  closed         {}".format(self.closed),
            "  digest         {}".format(short_hash(self.digest) or "-"),
            "  chain          {}".format("intact" if self.chain_ok else "BROKEN"),
            "  manifest match {}".format(self.digest_matches),
            "  blobs checked  {} ({})".format(
                self.blobs_checked, "ok" if self.blobs_ok else "PROBLEMS"
            ),
            "  seed           {}".format(self.seed),
        ]
        if self.counts:
            counts = ", ".join("{}={}".format(k, v) for k, v in sorted(self.counts.items()))
            lines.append("  kinds          {}".format(counts))
        if self.parent:
            lines.append(
                "  forked from    {} at seq {}".format(
                    self.parent.get("path"), self.parent.get("upto_seq")
                )
            )
        for problem in self.problems:
            lines.append("  ! {}".format(problem))
        return "\n".join(lines)


def verify(path: Any, *, check_blobs: bool = True, check_digest: bool = True) -> TapeReport:
    """Check the integrity of a tape.

    Walks the event chain recomputing each hash, confirms each event links to
    its predecessor, compares the head against the manifest digest, and (unless
    disabled) verifies that every referenced blob still matches its content
    address.

    Never raises for a damaged tape -- damage is reported in
    :attr:`TapeReport.problems`. It raises only when *path* is not a tape at all.

    Args:
        path: Tape directory.
        check_blobs: Verify blob contents against their digests.
        check_digest: Compare the computed head against the manifest digest.

    Returns:
        A :class:`TapeReport`.
    """
    tape = Tape.open(path)
    manifest = tape.manifest
    report = TapeReport(
        path=str(tape.path),
        format_version=int(manifest.get("format_version") or 0),
        closed=bool(manifest.get("closed")),
        manifest_digest=manifest.get("digest") or "",
        parent=manifest.get("parent"),
        seed=manifest.get("seed"),
        created_at=manifest.get("created_at"),
        agenttape_version=manifest.get("agenttape_version"),
    )

    prev = GENESIS
    counts: Dict[str, int] = {}
    blob_refs: List[str] = []
    head = GENESIS
    chain_ok = True
    index = 0

    try:
        for stored in tape.iter_stored():
            body = {k: v for k, v in stored.items() if k != "hash"}
            declared = stored.get("hash") or ""

            # Collect blob references from the *stored* body, before anything
            # that could fail. A tape with a damaged blob store must still be
            # checkable for tampering, and vice versa: the two failure modes
            # need to stay distinguishable.
            _collect_blob_refs(body, blob_refs)

            if body.get("prev") != prev:
                chain_ok = False
                report.first_bad_seq = index
                report.problems.append(
                    "seq {}: broken chain -- event claims predecessor {}, expected "
                    "{}".format(index, short_hash(body.get("prev") or ""), short_hash(prev))
                )
                break

            recomputed = event_hash(body)
            if recomputed != declared:
                chain_ok = False
                report.first_bad_seq = index
                report.problems.append(
                    "seq {}: hash mismatch -- stored {}, recomputed {} (the event "
                    "was modified)".format(index, short_hash(declared), short_hash(recomputed))
                )
                break

            kind = body.get("kind")
            if kind != EventKind.FOOTER:
                counts[kind] = counts.get(kind, 0) + 1

            prev = declared
            head = declared
            index += 1
    except TapeIntegrityError as exc:
        chain_ok = False
        report.problems.append(str(exc))

    report.events = index
    report.counts = counts
    report.chain_ok = chain_ok
    report.digest = head
    report.digest_matches = (head == report.manifest_digest) if check_digest else True

    if check_digest and not report.digest_matches:
        report.problems.append(
            "manifest digest {} does not match the computed head {}; the tape was "
            "modified after it was closed".format(
                short_hash(report.manifest_digest), short_hash(head)
            )
        )

    if check_blobs:
        report.blobs_checked = len(blob_refs)
        for ref in blob_refs:
            try:
                tape.get_blob(ref)
            except TapeIntegrityError as exc:
                report.blobs_ok = False
                report.missing_blobs.append(ref)
                report.problems.append(str(exc))

    report.ok = (
        report.chain_ok
        and report.digest_matches
        and report.blobs_ok
        and not report.problems
    )
    return report


def _collect_blob_refs(value: Any, out: List[str]) -> None:
    """Walk a stored body collecting blob digests."""
    if isinstance(value, dict):
        if value.get(TAG) == "blob":
            ref = value.get("value")
            if isinstance(ref, str):
                out.append(ref)
            return
        for item in value.values():
            _collect_blob_refs(item, out)
    elif isinstance(value, list):
        for item in value:
            _collect_blob_refs(item, out)


# --------------------------------------------------------------------------- #
# diff
# --------------------------------------------------------------------------- #


@dataclass
class TapeDiff:
    """Result of :func:`diff`."""

    a: str
    b: str
    identical: bool = False
    common_events: int = 0
    first_difference_seq: Optional[int] = None
    reason: str = ""
    expected: Optional[Dict[str, Any]] = None
    observed: Optional[Dict[str, Any]] = None
    detail: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Plain-dict form."""
        return {
            "a": self.a,
            "b": self.b,
            "identical": self.identical,
            "common_events": self.common_events,
            "first_difference_seq": self.first_difference_seq,
            "reason": self.reason,
            "expected": self.expected,
            "observed": self.observed,
            "detail": self.detail,
        }

    def render(self) -> str:
        """Human-readable multi-line summary."""
        if self.identical:
            return "tapes are equivalent ({} events compared)".format(self.common_events)
        lines = [
            "tapes diverge at seq {}: {}".format(self.first_difference_seq, self.reason),
            "  a: {}".format(self.a),
            "  b: {}".format(self.b),
        ]
        if self.detail:
            lines.append(self.detail)
        return "\n".join(lines)


def diff(a: Any, b: Any, *, strict: bool = False) -> TapeDiff:
    """Find the first point at which two tapes differ.

    The primary use is regression testing: record a golden run, replay it
    against changed agent code, and diff the resulting tapes. A diff at step 5
    is a much better bug report than "the output changed".

    Args:
        a: The reference tape (usually the recording).
        b: The tape to compare against it.
        strict: Also compare recorded *responses*. By default only the call
            sequence (kind, name, request key) is compared, because responses
            legitimately differ between a recording and a live re-run.

    Returns:
        A :class:`TapeDiff`. ``identical`` is ``True`` when no difference was
        found within the common prefix and both tapes have the same length.
    """
    tape_a = Tape.open(a)
    tape_b = Tape.open(b)
    result = TapeDiff(a=str(tape_a.path), b=str(tape_b.path))

    stream_a = _calls(tape_a)
    stream_b = _calls(tape_b)

    index = 0
    while True:
        event_a = next(stream_a, None)
        event_b = next(stream_b, None)

        if event_a is None and event_b is None:
            result.identical = True
            result.common_events = index
            return result

        if event_a is None:
            result.first_difference_seq = index
            result.reason = "the second tape is longer"
            result.observed = event_b.identity()
            return result

        if event_b is None:
            result.first_difference_seq = index
            result.reason = "the first tape is longer"
            result.expected = event_a.identity()
            return result

        if event_a.identity() != event_b.identity():
            result.first_difference_seq = index
            result.reason = "different call"
            result.expected = event_a.identity()
            result.observed = event_b.identity()
            result.detail = request_diff(event_a.request, event_b.request)
            return result

        if strict and event_a.response != event_b.response:
            result.first_difference_seq = index
            result.reason = "same call, different recorded response"
            result.expected = {"response": event_a.response}
            result.observed = {"response": event_b.response}
            result.detail = request_diff(event_a.response, event_b.response)
            return result

        index += 1


def _calls(tape: Tape):
    """Yield only the agent-call events of a tape (skipping tape metadata)."""
    for event in tape.iter_events():
        if event.kind in (EventKind.RECORD, EventKind.FOOTER, EventKind.FORK):
            continue
        yield event


# --------------------------------------------------------------------------- #
# check_determinism
# --------------------------------------------------------------------------- #


@dataclass
class DeterminismReport:
    """Result of :func:`check_determinism`."""

    tape: str
    repeats: int
    deterministic: bool = False
    runs: List[Dict[str, Any]] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Plain-dict form."""
        return {
            "tape": self.tape,
            "repeats": self.repeats,
            "deterministic": self.deterministic,
            "runs": self.runs,
            "problems": self.problems,
        }

    def render(self) -> str:
        """Human-readable multi-line summary."""
        lines = [
            "determinism check on {}".format(self.tape),
            "  verdict   {}".format("DETERMINISTIC" if self.deterministic else "NOT REPRODUCIBLE"),
            "  repeats   {}".format(self.repeats),
        ]
        for run in self.runs:
            lines.append(
                "  run {n}: consumed {c}, remaining {r}, outcome {o}, states {s}".format(
                    n=run.get("run"),
                    c=run.get("consumed"),
                    r=run.get("remaining"),
                    o=short_hash(run.get("outcome_fingerprint") or "") or "-",
                    s=len(run.get("states") or []),
                )
            )
        for problem in self.problems:
            lines.append("  ! {}".format(problem))
        return "\n".join(lines)


def check_determinism(
    tape_path: Any,
    run: Callable[[Session], Any],
    *,
    repeats: int = 2,
    strict: bool = True,
    on_divergence: str = "raise",
) -> DeterminismReport:
    """Confirm that a recording actually replays reproducibly.

    This is the self-test for a tape. It runs *run* against the tape *repeats*
    times and checks that each pass:

    1. consumes every recorded event (nothing was skipped, nothing ran short),
    2. produces the same outcome fingerprint,
    3. produces the same state fingerprints in the same order,
    4. raises no divergence.

    A tape that fails this is not a usable recording: there is a source of
    non-determinism the session is not wrapping, and the fix is to route it
    through the session rather than to loosen the check.

    Args:
        tape_path: Tape to test.
        run: Callable taking a :class:`~agenttape.Session` and driving the agent.
        repeats: Number of replay passes. Two is enough to catch order-dependence;
            three or more catches rarer interleavings.
        strict: Passed through to :meth:`Session.replay`.
        on_divergence: Passed through to :meth:`Session.replay`.

    Returns:
        A :class:`DeterminismReport`.
    """
    report = DeterminismReport(tape=str(tape_path), repeats=repeats)

    for index in range(repeats):
        session = Session.replay(tape_path, strict=strict, on_divergence=on_divergence)
        record: Dict[str, Any] = {"run": index + 1}

        try:
            value = run(session)
            record["return_fingerprint"] = fingerprint("return", value, strict=False)
            record["return"] = _safe_render(value)
        except Exception as exc:
            record["error"] = "{}: {}".format(type(exc).__name__, exc)
            report.problems.append("run {}: agent raised {}".format(index + 1, record["error"]))

        try:
            session.close(check=True)
        except DivergenceError as exc:
            report.problems.append("run {}: {}".format(index + 1, exc))
        except AgentTapeError as exc:
            report.problems.append("run {}: {}".format(index + 1, exc))

        record["consumed"] = session.consumed
        record["remaining"] = session.remaining
        record["outcome_fingerprint"] = session.outcome_fingerprint
        record["states"] = session.state_fingerprints
        record["divergences"] = session.divergences
        report.runs.append(record)

    if report.runs:
        first = _comparable(report.runs[0])
        for run in report.runs[1:]:
            if _comparable(run) != first:
                report.problems.append(
                    "run {} produced a different trajectory than run 1".format(run["run"])
                )
        for run in report.runs:
            if run.get("remaining"):
                report.problems.append(
                    "run {} left {} recorded event(s) unconsumed".format(
                        run["run"], run["remaining"]
                    )
                )

    report.deterministic = not report.problems
    return report


def _comparable(run: Dict[str, Any]) -> Dict[str, Any]:
    """Strip the run index so two runs can be compared for equality."""
    return {k: v for k, v in run.items() if k not in ("run", "return")}


def _safe_render(value: Any, limit: int = 200) -> str:
    """Render a return value for a report without risking a canonicalisation error."""
    try:
        from .canonical import canonical_dumps

        text = canonical_dumps(value, strict=False)
    except Exception:  # pragma: no cover - defensive
        text = repr(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."
