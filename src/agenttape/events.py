"""The event model: the atoms a tape is made of.

Every boundary crossing an agent makes is recorded as one :class:`Event`. The
event stream is append-only and hash-chained, so a tape is simultaneously a
debugging artifact and a tamper-evident record of what happened.

Design notes
------------
``key`` vs ``hash``
    ``key`` is the *request fingerprint* -- ``fingerprint(kind, name,
    request)``. It answers "is this the same call as that one?" and is what
    replay matches on.

    ``hash`` is the *event hash* -- the link in the chain. It covers the whole
    logical body including the previous event's hash. It answers "has this tape
    been altered?" and is what :func:`agenttape.verify` checks.

``prev``
    The hash of the preceding event, or :data:`GENESIS` for the first event.
    Because each hash folds in its predecessor, altering any event invalidates
    every event after it.

``status`` / ``error``
    A call that raised is recorded with ``status="error"`` and a portable error
    record. Failures are first-class events, not gaps: a tool that timed out is
    usually the most interesting thing in the tape, and replay re-raises the
    same exception so the agent's control flow is reproduced faithfully.
"""

from __future__ import annotations

import builtins
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .canonical import canonical_dumps, sha256_hex

__all__ = ["GENESIS", "EventKind", "Event", "rebuild_error", "event_hash"]

#: The ``prev`` value of the first event in a tape (64 zeroes).
GENESIS = "0" * 64


def event_hash(body: Dict[str, Any]) -> str:
    """Hash an event body. This is the link in the tape's hash chain.

    The body passed in must be the body *as stored* -- that is, after oversized
    payloads have been replaced by blob references. Hashing the stored form
    rather than the logical form is deliberate: it makes integrity verification
    a purely local operation over the event log, so a tape whose blob store has
    been damaged can still be checked for tampering, and the two failure modes
    stay distinguishable. Blob *contents* are covered transitively, because a
    blob reference embeds the SHA-256 of the bytes it points at.

    ``hash`` itself must be excluded from *body*: an event cannot hash its own
    hash. ``prev`` must be included, which is what makes the chain a chain.
    """
    return sha256_hex(canonical_dumps(body).encode("utf-8"))


class EventKind:
    """String constants for the kinds of event a tape can contain.

    Plain strings rather than an ``Enum`` so that tapes remain forward
    compatible: a reader from an older version sees an unknown kind as an
    ordinary string instead of crashing on a failed enum lookup.
    """

    #: Sequence 0. Captures the environment the run was recorded in.
    RECORD = "record"
    #: A model / LLM call.
    MODEL = "model"
    #: A tool call.
    TOOL = "tool"
    #: A read of the wall clock or monotonic clock.
    CLOCK = "clock"
    #: A draw from the random number generator.
    RANDOM = "random"
    #: A read of ambient environment: env var, file, network resource.
    ENV = "env"
    #: A side-effecting action, optionally paired with a compensation.
    EFFECT = "effect"
    #: The application of a compensation during :meth:`agenttape.Session.rollback`.
    COMPENSATE = "compensate"
    #: Written automatically when a replay runs past its recording and continues
    #: live. Tape metadata, not an agent call: replay skips it.
    FORK = "fork"
    #: A state snapshot or state fingerprint.
    STATE = "state"
    #: The run's final result, recorded so replays can be compared.
    OUTCOME = "outcome"
    #: A user-supplied annotation.
    MARK = "mark"
    #: A free-form note.
    LOG = "log"
    #: The last event of a closed tape.
    FOOTER = "footer"

    #: Every kind, for validation and reporting.
    ALL = (
        RECORD, MODEL, TOOL, CLOCK, RANDOM, ENV, EFFECT,
        COMPENSATE, FORK, STATE, OUTCOME, MARK, LOG, FOOTER,
    )


@dataclass
class Event:
    """One recorded boundary crossing.

    Instances are plain mutable containers because the tape writer fills in
    ``hash`` after constructing the body. Treat them as read-only once read
    back from disk.
    """

    seq: int
    kind: str
    name: str
    key: str
    request: Any = None
    response: Any = None
    ts: float = 0.0
    status: str = "ok"
    error: Optional[Dict[str, Any]] = None
    duration: Optional[float] = None
    meta: Dict[str, Any] = field(default_factory=dict)
    prev: str = GENESIS
    hash: str = ""

    # -- construction ------------------------------------------------------- #

    def body(self) -> Dict[str, Any]:
        """Return the logical body that :attr:`hash` is computed over.

        Excludes ``hash`` itself (an event cannot hash its own hash) and
        includes ``prev``, which is what makes the chain a chain.
        """
        return {
            "seq": self.seq,
            "kind": self.kind,
            "name": self.name,
            "ts": self.ts,
            "key": self.key,
            "request": self.request,
            "response": self.response,
            "status": self.status,
            "error": self.error,
            "duration": self.duration,
            "meta": self.meta,
            "prev": self.prev,
        }

    def compute_hash(self) -> str:
        """Hash of this event's *logical* body.

        Only equal to the hash stored on the tape when no payload was large
        enough to be replaced by a blob reference. Use :func:`event_hash` on the
        stored body for anything to do with tape integrity.
        """
        return event_hash(self.body())

    @classmethod
    def from_body(cls, body: Dict[str, Any], *, hash: str = "") -> "Event":
        """Build an event from a decoded tape body, tolerating unknown fields.

        Unknown keys land in ``meta["_unknown"]`` rather than raising, so a tape
        written by a newer version of the library is still readable.
        """
        known = {
            "seq", "kind", "name", "ts", "key", "request", "response",
            "status", "error", "duration", "meta", "prev",
        }
        data = {k: v for k, v in body.items() if k in known}
        extra = {k: v for k, v in body.items() if k not in known}
        meta = dict(data.pop("meta", None) or {})
        if extra:
            meta.setdefault("_unknown", {}).update(extra)
        event = cls(**data)
        event.meta = meta
        event.hash = hash
        return event

    # -- convenience -------------------------------------------------------- #

    @property
    def ok(self) -> bool:
        """``True`` if the recorded call succeeded."""
        return self.status == "ok"

    def label(self) -> str:
        """Short human-readable label, e.g. ``#7 tool:search``."""
        return "#{} {}:{}".format(self.seq, self.kind, self.name)

    def summary(self, limit: int = 80) -> str:
        """One-line description, with payloads truncated for logs."""
        detail = canonical_dumps(self.request, strict=False)
        if len(detail) > limit:
            detail = detail[: limit - 3] + "..."
        marker = "" if self.ok else " [{}]".format(
            (self.error or {}).get("type", "error")
        )
        return "{} {}{}".format(self.label(), detail, marker)

    def to_dict(self) -> Dict[str, Any]:
        """Return the stored form (logical body plus ``hash``)."""
        out = self.body()
        out["hash"] = self.hash
        return out

    # -- matching ----------------------------------------------------------- #

    def matches(self, kind: str, name: str, key: str) -> bool:
        """Return ``True`` if this event is the answer to the given call."""
        return self.kind == kind and self.name == name and self.key == key

    def identity(self) -> Dict[str, str]:
        """The triple used for matching, for use in divergence reports."""
        return {"kind": self.kind, "name": self.name, "key": self.key}

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return "<Event {} {}>".format(self.summary(), self.hash[:12])


def rebuild_error(record: Dict[str, Any]) -> BaseException:
    """Reconstruct an exception from its recorded form.

    Replay must re-raise the *same type* a tool originally raised, because
    agent control flow branches on it (``except TimeoutError:``). The class is
    resolved only from modules that are **already imported**; nothing is
    imported on a tape's behalf, so a hostile tape cannot cause an import.

    Falls back to :class:`~agenttape.errors.RecordedError` when the class cannot
    be found or does not behave like an exception.
    """
    from .errors import RecordedError

    if not isinstance(record, dict):
        return RecordedError({"message": str(record)})

    type_name = record.get("type")
    module_name = record.get("module")
    message = record.get("message", "")

    if type_name:
        candidates = []
        if module_name:
            module = sys.modules.get(module_name)
            if module is not None:
                candidates.append(getattr(module, type_name, None))
        candidates.append(getattr(builtins, type_name, None))
        for candidate in candidates:
            if isinstance(candidate, type) and issubclass(candidate, BaseException):
                try:
                    exc = candidate(message)
                except Exception:  # pragma: no cover - exotic __init__ signatures
                    break
                setattr(exc, "agenttape_record", record)
                return exc

    return RecordedError(record)
