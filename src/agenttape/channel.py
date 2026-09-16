"""The two channels a session can run in: recording and replaying.

A *channel* is the thing that actually answers an agent's boundary calls.
:class:`Recorder` answers by invoking the real callable and writing the result
down. :class:`Replayer` answers by reading the answer back off the tape -- and
**never** invokes the callable.

The replayer is where the interesting decisions live
---------------------------------------------------

*Matching is sequence-primary with fingerprint validation.* The *n*-th call of a
replay must equal the *n*-th call of the recording. This is the strongest
available correctness property: any deviation is a real deviation, not a
tolerated reordering. It also makes the error message useful, because a
mismatch at position *n* means the run diverged at *n*, not merely that some
response somewhere was missing.

*Fingerprints are validated as well as positions*, so the engine can say **what**
changed -- the prompt, the tool arguments, the temperature -- rather than only
**that** something did.

*Replay never guesses.* If a call cannot be matched, the engine raises. It does
not fall through to a live system, because a replay engine that silently
substitutes a live answer is worse than no replay engine at all: it manufactures
false confidence.
"""

from __future__ import annotations

import difflib
import json
import time
from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Optional

from .canonical import canonical_dumps, to_canonical
from .errors import (
    RequestMismatchError,
    SessionStateError,
    TapeExhaustedError,
    UnconsumedEventsError,
)
from .events import Event, EventKind

if TYPE_CHECKING:  # pragma: no cover
    from .tape import Tape

__all__ = ["UNSET", "Recorder", "Replayer"]


class _Unset:
    """Sentinel distinguishing "no response supplied" from ``response=None``."""

    _instance = None

    def __new__(cls) -> "_Unset":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "UNSET"


#: Sentinel: "the caller did not supply a response".
UNSET = _Unset()


# --------------------------------------------------------------------------- #
# Recording
# --------------------------------------------------------------------------- #


class Recorder:
    """Executes real calls and writes each one to the tape before returning."""

    def __init__(self, tape: "Tape") -> None:
        self._tape = tape
        self.counts: Dict[str, int] = {}
        self.errors = 0
        self.total_duration = 0.0
        self.last_event: Optional[Event] = None

    @property
    def tape(self) -> "Tape":
        return self._tape

    def exchange(
        self,
        kind: str,
        name: str,
        key: str,
        request: Any,
        *,
        fn: Optional[Any] = None,
        response: Any = UNSET,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Run ``fn()`` (or take ``response``), record it, and return it.

        If ``fn`` raises, the failure is recorded with ``status="error"`` and the
        original exception is re-raised with its traceback intact. Failures are
        first-class tape content: a tool that timed out is usually the most
        interesting event in the run.

        Raises:
            SessionStateError: If neither ``fn`` nor ``response`` was supplied.
        """
        if fn is None and response is UNSET:
            raise SessionStateError(
                "recording {!r} ({}) needs either an fn to execute or an explicit "
                "response=".format(name, kind)
            )

        started_at = time.time()
        started = time.perf_counter()

        if fn is not None:
            try:
                value = fn()
            except Exception as exc:
                duration = time.perf_counter() - started
                self.last_event = self._tape.append(
                    kind=kind,
                    name=name,
                    key=key,
                    request=request,
                    response=None,
                    ts=started_at,
                    status="error",
                    error={
                        "type": type(exc).__name__,
                        "module": type(exc).__module__,
                        "message": str(exc),
                        "repr": repr(exc),
                    },
                    duration=duration,
                    meta=meta,
                )
                self._bump(kind, duration)
                self.errors += 1
                raise
        else:
            value = response

        duration = time.perf_counter() - started
        self.last_event = self._tape.append(
            kind=kind,
            name=name,
            key=key,
            request=request,
            response=value,
            ts=started_at,
            status="ok",
            error=None,
            duration=duration,
            meta=meta,
        )
        self._bump(kind, duration)
        return value

    def _bump(self, kind: str, duration: float) -> None:
        self.counts[kind] = self.counts.get(kind, 0) + 1
        self.total_duration += duration


# --------------------------------------------------------------------------- #
# Replaying
# --------------------------------------------------------------------------- #


class Replayer:
    """Serves recorded answers, in order, without ever invoking the callable.

    The event log is streamed rather than loaded, so memory use stays O(1) in
    strict mode (the default). Lenient mode may buffer up to ``lookahead``
    events while searching for a match.
    """

    #: Event kinds that are tape metadata rather than agent calls.
    NON_CALL_KINDS = (EventKind.RECORD, EventKind.FOOTER, EventKind.FORK)

    #: How many leftover events to buffer before reporting an inexact count.
    DRAIN_LIMIT = 64

    def __init__(
        self,
        tape: "Tape",
        *,
        strict: bool = True,
        lookahead: int = 256,
        on_divergence: str = "raise",
    ) -> None:
        if on_divergence not in ("raise", "warn"):
            raise ValueError(
                "on_divergence must be 'raise' or 'warn', got {!r}".format(on_divergence)
            )
        self._tape = tape
        self._stream: Iterator[Event] = tape.iter_events()
        self._buffer: List[Event] = []
        self._skipped: List[Event] = []
        self._stream_done = False
        self._strict = strict
        self._lookahead = max(1, int(lookahead))
        self._on_divergence = on_divergence

        self.header: Optional[Event] = None
        self.footer: Optional[Event] = None
        self.fork_markers: List[Event] = []
        self.consumed = 0
        self.total = 0
        self.divergences: List[Dict[str, Any]] = []

    @property
    def tape(self) -> "Tape":
        return self._tape

    @property
    def strict(self) -> bool:
        return self._strict

    @property
    def unconsumed(self) -> List[Event]:
        """Events read but never matched, plus events never reached."""
        return list(self._skipped) + list(self._buffer)

    # -- stream plumbing ---------------------------------------------------- #

    def _fill(self, minimum: int = 1) -> None:
        """Pull from the stream until the buffer holds *minimum* events or is dry."""
        while len(self._buffer) < minimum and not self._stream_done:
            try:
                event = next(self._stream)
            except StopIteration:
                self._stream_done = True
                return
            if event.kind == EventKind.FOOTER:
                self.footer = event
                self._stream_done = True
                return
            if event.kind == EventKind.RECORD and self.header is None and not self._buffer:
                self.header = event
                continue
            if event.kind == EventKind.FORK:
                # Written by a previous live tail. It marks where a recording
                # ended; the agent never issued it, so it is not a call.
                self.fork_markers.append(event)
                continue
            self._buffer.append(event)
            self.total += 1

    def _consume(self) -> Event:
        event = self._buffer.pop(0)
        self.consumed += 1
        return event

    # -- the one method the session needs ----------------------------------- #

    def next_event(self, kind: str, name: str, key: str, request: Any) -> Event:
        """Return the recorded event answering this call.

        Raises:
            RequestMismatchError: In strict mode, when the call does not match
                the next recorded event.
            TapeExhaustedError: When the tape has no answer left.
        """
        self._fill(1)
        expected = self._buffer[0] if self._buffer else None

        # Fast path: the call matches the next event in the recording.
        if expected is not None and expected.matches(kind, name, key):
            return self._consume()

        if self._strict and self._on_divergence == "raise":
            if expected is None:
                raise self._exhausted(kind, name, key, ran_past_end=True)
            raise self._mismatch(expected, kind, name, key, request)

        found = self._search(kind, name, key)
        if found is not None:
            self._record_divergence(expected, kind, name, key, request, recovered=True)
            return found

        raise self._exhausted(kind, name, key, ran_past_end=expected is None)

    def _exhausted(
        self, kind: str, name: str, key: str, *, ran_past_end: bool
    ) -> TapeExhaustedError:
        if ran_past_end:
            message = (
                "replay ran past the end of the tape: the agent made a call "
                "({}:{}) with no recorded counterpart. The replayed run is longer "
                "than the recorded run.".format(kind, name)
            )
        else:
            message = (
                "replay could not find a recorded counterpart for {}:{} anywhere in "
                "the tape.".format(kind, name)
            )
        return TapeExhaustedError(
            message,
            seq=self.total,
            observed={"kind": kind, "name": name, "key": key},
        )

    def _search(self, kind: str, name: str, key: str) -> Optional[Event]:
        """Lenient lookup: scan already-skipped events, then forward.

        Events that were passed over earlier are checked first, because they
        precede the current position in the recording. Forward scanning is
        bounded by ``lookahead`` so that a badly mismatched tape cannot pull an
        unbounded amount of it into memory.
        """
        while True:
            for index, event in enumerate(self._skipped):
                if event.matches(kind, name, key):
                    self._skipped.pop(index)
                    self.consumed += 1
                    return event

            for index, event in enumerate(self._buffer):
                if event.matches(kind, name, key):
                    self._skipped.extend(self._buffer[:index])
                    del self._buffer[:index]
                    return self._consume()

            if self._stream_done or len(self._buffer) >= self._lookahead:
                return None
            self._fill(len(self._buffer) + 1)

    # -- divergence reporting ----------------------------------------------- #

    def _mismatch(
        self, expected: Event, kind: str, name: str, key: str, request: Any
    ) -> RequestMismatchError:
        detail_lines = ["expected {}".format(expected.summary())]
        if expected.kind != kind or expected.name != name:
            detail_lines.append("observed {}:{} (different kind/name)".format(kind, name))
        else:
            detail_lines.append(
                "observed {}:{} (same call, different arguments)".format(kind, name)
            )
            diff = request_diff(expected.request, request)
            if diff:
                detail_lines.append(diff)
        error = RequestMismatchError(
            "replay diverged at seq {}: the agent's call does not match the "
            "recording".format(expected.seq),
            seq=expected.seq,
            expected=expected.identity(),
            observed={"kind": kind, "name": name, "key": key},
            detail="\n".join(detail_lines),
        )
        self._record_divergence(expected, kind, name, key, request, recovered=False)
        return error

    def _record_divergence(
        self,
        expected: Optional[Event],
        kind: str,
        name: str,
        key: str,
        request: Any,
        *,
        recovered: bool,
    ) -> None:
        self.divergences.append(
            {
                "seq": expected.seq if expected is not None else self.total,
                "expected": expected.identity() if expected is not None else None,
                "observed": {"kind": kind, "name": name, "key": key},
                "expected_request": expected.request if expected is not None else None,
                "observed_request": request,
                "recovered": recovered,
                "strict": self._strict,
            }
        )

    # -- completion --------------------------------------------------------- #

    def finish(self) -> List[Event]:
        """Drain enough of the stream to report what is left, without raising.

        Replay streams rather than loads, so at any moment the "remaining" count
        only covers what has been pulled. Finishing the stream (up to
        :data:`DRAIN_LIMIT`) is what makes the count meaningful -- and what lets
        :meth:`close` release the underlying file handle.

        Returns:
            The unconsumed events. A non-empty list means the replayed run was
            shorter than the recorded run.
        """
        if not self._stream_done:
            while not self._stream_done and len(self._buffer) < self.DRAIN_LIMIT:
                self._fill(len(self._buffer) + 1)
        return self.unconsumed

    def assert_exhausted(self, *, require_full: bool = True) -> None:
        """Verify the replay consumed the whole tape.

        A run that finishes while events remain has taken a *shorter* path than
        the recording. That is a divergence, and it must be loud: a silently
        truncated replay looks like success.

        Raises:
            UnconsumedEventsError: If events remain and *require_full* is true.
        """
        leftover = self.finish()
        if leftover and require_full:
            exact = "" if self._stream_done else "at least "
            preview = ", ".join(event.label() for event in leftover[:5])
            more = "" if len(leftover) <= 5 else " (+{} more)".format(len(leftover) - 5)
            raise UnconsumedEventsError(
                "replay finished with {}{} unconsumed recorded event(s): {}{}. The "
                "replayed run is shorter than the recorded run.".format(
                    exact, len(leftover), preview, more
                ),
                remaining=leftover,
            )

    def close(self) -> None:
        """Release the underlying file handle.

        The event stream is a generator over an open file. Closing it explicitly
        matters on Windows, where a live handle blocks the tape directory from
        being moved or deleted.
        """
        stream = self._stream
        if hasattr(stream, "close"):
            try:
                stream.close()  # type: ignore[union-attr]
            except Exception:  # pragma: no cover - defensive
                pass

    def summary(self) -> Dict[str, Any]:
        """Counters describing what this replayer did."""
        return {
            "consumed": self.consumed,
            "remaining": len(self.unconsumed),
            "divergences": len(self.divergences),
            "strict": self._strict,
        }


# --------------------------------------------------------------------------- #
# Diffs
# --------------------------------------------------------------------------- #


def request_diff(recorded: Any, observed: Any, *, context: int = 1) -> str:
    """Return a unified diff between a recorded and an observed request.

    Falls back to a plain "they differ" message when the payloads cannot be
    rendered, so the caller always gets *something* actionable.
    """
    try:
        left = json.dumps(to_canonical(recorded, strict=False), indent=2, sort_keys=True)
        right = json.dumps(to_canonical(observed, strict=False), indent=2, sort_keys=True)
    except Exception:  # pragma: no cover - defensive
        return "  (requests differ and could not be rendered)"

    if left == right:
        return "  (payloads are structurally identical; the difference is in the key)"

    lines = list(
        difflib.unified_diff(
            left.splitlines(),
            right.splitlines(),
            fromfile="recorded",
            tofile="replayed",
            lineterm="",
            n=context,
        )
    )
    if len(lines) > 40:
        lines = lines[:40] + ["  ... (diff truncated)"]
    return "\n".join("  " + line for line in lines)


def describe_request(value: Any, limit: int = 160) -> str:
    """Compact one-line rendering of a request payload, for logs."""
    text = canonical_dumps(value, strict=False)
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text
