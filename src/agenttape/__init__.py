"""agenttape -- the 1Agent tape recorder.

A library for recording AI agent runs to local files and replaying them offline,
deterministically.

The problem
-----------

An agent run is not reproducible. Re-run the same program against the same task
and you get a different trajectory: the model samples differently, the tools
return different data, the clock has moved on. That makes production failures
undebuggable, prompt changes untestable, and retries dangerous.

What this library does
----------------------

``agenttape`` wraps every boundary an agent crosses and writes the answers to a
**tape** -- an append-only, hash-chained, self-describing log on disk. Replaying
a tape re-runs the agent's *real logic* while every external answer is served
from the recording: no model calls, no tool execution, no side effects, no cost.

What it does not do
-------------------

It cannot make an arbitrary agent deterministic. It can only replay what it
recorded, so any unwrapped path -- a bare HTTP client, a subprocess, a C
extension reading entropy -- is a hole in the recording. It does not solve
concurrency or streaming replay, and it cannot undo side effects that have no
inverse. See ``research/deterministic-replay-and-rollback-for-ai-agents.md`` for
what remains an open problem.

Quick start
-----------

Record::

    import agenttape

    def run_agent(session):
        hits = session.tool("search", ["refund policy"], fn=lambda: search("refund policy"))
        answer = session.model(
            "gpt-4o",
            {"messages": [{"role": "user", "content": hits}]},
            fn=lambda: call_model(hits),
        )
        session.outcome({"answer": answer})
        return answer

    with agenttape.record("runs/triage.tape", overwrite=True) as session:
        run_agent(session)

Replay -- identical trajectory, offline, free::

    with agenttape.replay("runs/triage.tape") as session:
        run_agent(session)          # search() and call_model() are never invoked

Verify::

    print(agenttape.verify("runs/triage.tape").render())

Public API
----------

Recording and replay
    :class:`Session`, :func:`record`, :func:`replay`

Storage
    :class:`Tape`, :class:`Event`, :class:`EventKind`

Deterministic primitives
    :class:`DeterministicClock`, :class:`DeterministicRandom`

Verification
    :func:`verify`, :func:`diff`, :func:`check_determinism`, :class:`TapeReport`,
    :class:`TapeDiff`, :class:`DeterminismReport`

Serialisation
    :func:`canonical_dumps`, :func:`canonical_loads`, :func:`to_canonical`,
    :func:`fingerprint`

Errors
    :class:`AgentTapeError` and its subclasses; every replay-time failure is a
    :class:`DivergenceError`
"""

from __future__ import annotations

from ._version import TAPE_FORMAT_VERSION, VERSION_INFO, __version__
from .canonical import (
    ENCODER_ATTR,
    TAG,
    canonical_dumps,
    canonical_loads,
    fingerprint,
    from_canonical,
    sha256_hex,
    short_hash,
    to_canonical,
)
from .channel import UNSET, Recorder, Replayer, request_diff
from .clock import DeterministicClock
from .entropy import DeterministicRandom
from .errors import (
    AgentTapeError,
    CanonicalizationError,
    CompensationError,
    DivergenceError,
    RecordedError,
    RequestMismatchError,
    SessionStateError,
    TapeError,
    TapeExhaustedError,
    TapeFormatError,
    TapeIntegrityError,
    UnconsumedEventsError,
)
from .events import GENESIS, Event, EventKind, rebuild_error
from .session import Session, record, replay
from .tape import EVENTS_NAME, MANIFEST_NAME, Tape
from .verify import (
    DeterminismReport,
    TapeDiff,
    TapeReport,
    check_determinism,
    diff,
    verify,
)

__all__ = [
    # version
    "__version__",
    "VERSION_INFO",
    "TAPE_FORMAT_VERSION",
    # recording and replay
    "Session",
    "record",
    "replay",
    "Recorder",
    "Replayer",
    "UNSET",
    # storage
    "Tape",
    "Event",
    "EventKind",
    "GENESIS",
    "MANIFEST_NAME",
    "EVENTS_NAME",
    # deterministic primitives
    "DeterministicClock",
    "DeterministicRandom",
    # verification
    "verify",
    "diff",
    "check_determinism",
    "TapeReport",
    "TapeDiff",
    "DeterminismReport",
    "request_diff",
    # serialisation
    "canonical_dumps",
    "canonical_loads",
    "to_canonical",
    "from_canonical",
    "fingerprint",
    "sha256_hex",
    "short_hash",
    "TAG",
    "ENCODER_ATTR",
    # errors
    "AgentTapeError",
    "TapeError",
    "TapeFormatError",
    "TapeIntegrityError",
    "CanonicalizationError",
    "DivergenceError",
    "RequestMismatchError",
    "TapeExhaustedError",
    "UnconsumedEventsError",
    "SessionStateError",
    "CompensationError",
    "RecordedError",
    "rebuild_error",
]
