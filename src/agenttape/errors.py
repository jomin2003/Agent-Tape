"""Exception hierarchy for :mod:`agenttape`.

The hierarchy is deliberately shallow and has one important property: **every
replay-time failure is a** :class:`DivergenceError`. Callers that only care about
"did replay faithfully reproduce the recording?" can therefore write::

    try:
        with agenttape.replay(path) as session:
            run_agent(session)
    except agenttape.DivergenceError as exc:
        report_bug(exc)

without having to enumerate the individual failure modes.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

__all__ = [
    "AgentTapeError",
    "TapeError",
    "TapeFormatError",
    "TapeIntegrityError",
    "TapeExhaustedError",
    "CanonicalizationError",
    "DivergenceError",
    "RequestMismatchError",
    "UnconsumedEventsError",
    "SessionStateError",
    "CompensationError",
    "RecordedError",
]


class AgentTapeError(Exception):
    """Base class for every error raised by :mod:`agenttape`."""


# --------------------------------------------------------------------------- #
# Tape / storage errors
# --------------------------------------------------------------------------- #


class TapeError(AgentTapeError):
    """Base class for problems with the tape itself (I/O, layout, lifecycle)."""


class TapeFormatError(TapeError):
    """The tape is not a readable agenttape, or uses an unsupported version."""


class TapeIntegrityError(TapeError):
    """The tape's hash chain, digest, or blob store failed verification.

    This means the bytes on disk are not the bytes that were written: the tape
    was truncated mid-line, edited, or corrupted.
    """

    def __init__(self, message: str, *, seq: Optional[int] = None) -> None:
        super().__init__(message)
        self.seq = seq


# --------------------------------------------------------------------------- #
# Serialisation
# --------------------------------------------------------------------------- #


class CanonicalizationError(AgentTapeError):
    """A value could not be converted to a stable, canonical representation.

    Raised in strict mode when an object has no deterministic encoding. The
    message names the offending type and suggests the fix (implement
    ``__agenttape_encode__``).
    """


# --------------------------------------------------------------------------- #
# Replay / divergence
# --------------------------------------------------------------------------- #


class DivergenceError(AgentTapeError):
    """Base class for "the replayed run did not match the recording".

    Carries enough structured detail to print a useful diff: which sequence
    number diverged, what was expected, and what was actually observed.
    """

    kind = "divergence"

    def __init__(
        self,
        message: str,
        *,
        seq: Optional[int] = None,
        expected: Any = None,
        observed: Any = None,
        detail: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.seq = seq
        self.expected = expected
        self.observed = observed
        self.detail = detail

    def as_dict(self) -> dict:
        """Machine-readable form, suitable for JSON reports and CI output."""
        return {
            "kind": self.kind,
            "message": str(self),
            "seq": self.seq,
            "expected": self.expected,
            "observed": self.observed,
            "detail": self.detail,
        }

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        base = super().__str__()
        if self.detail:
            return "{}\n{}".format(base, self.detail)
        return base


class RequestMismatchError(DivergenceError):
    """The agent issued a call that does not match the recorded event.

    The usual causes: the agent's code changed, a prompt changed, an unwrapped
    source of non-determinism leaked into the request, or two concurrent calls
    were reordered.
    """

    kind = "request_mismatch"


class TapeExhaustedError(DivergenceError):
    """The agent asked for more recorded events than the tape contains.

    The replayed run is *longer* than the recorded run. Either the agent now
    takes extra steps, or a boundary was recorded incompletely.
    """

    kind = "tape_exhausted"


class UnconsumedEventsError(DivergenceError):
    """The replayed run finished while recorded events remained.

    The replayed run is *shorter* than the recorded run. Raised from
    :meth:`agenttape.Session.close` so that a silently-truncated replay cannot
    pass as a successful one.
    """

    kind = "unconsumed_events"

    def __init__(self, message: str, *, remaining: Sequence[Any] = ()) -> None:
        super().__init__(message)
        self.remaining = list(remaining)


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #


class SessionStateError(AgentTapeError):
    """A session method was called in the wrong mode, or after ``close()``."""


class CompensationError(AgentTapeError):
    """A compensating action for a recorded effect could not be applied."""


class RecordedError(AgentTapeError):
    """Fallback used when a recorded exception cannot be reconstructed.

    When a tool raised ``FooError`` during recording, replay re-raises
    ``FooError`` if its module is importable. If it is not (the class was
    deleted, the module renamed, the tape came from another machine), replay
    raises :class:`RecordedError` instead, preserving the original type name and
    message in :attr:`record` so the failure is still diagnosable.
    """

    def __init__(self, record: dict) -> None:
        message = record.get("message") or record.get("repr") or "recorded error"
        super().__init__(
            "{}: {} (reconstructed as RecordedError; original type {!r} was not "
            "importable)".format(record.get("type", "Error"), message, record.get("type"))
        )
        self.record = record
