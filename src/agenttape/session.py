"""The session facade: the API most users touch.

A :class:`Session` is the object an agent program is handed. It exposes every
non-deterministic boundary as an explicit, recorded method:

===================  ==========================================================
Boundary             Method
===================  ==========================================================
Model calls          :meth:`~Session.model`, :meth:`~Session.exchange`
Tool calls           :meth:`~Session.tool`
Wall clock           :attr:`~Session.clock` (``.time()``, ``.now()``, ...)
Randomness           :attr:`~Session.rng`
Environment          :meth:`~Session.env`, :meth:`~Session.read_text`
Identifiers          :meth:`~Session.uuid4`, :meth:`~Session.token_hex`
Side effects         :meth:`~Session.effect`, :meth:`~Session.rollback`
State checkpoints    :meth:`~Session.state`
Annotations          :meth:`~Session.mark`, :meth:`~Session.log`
===================  ==========================================================

The same agent code runs unchanged in both modes::

    with agenttape.record("runs/triage.tape") as session:
        answer = run_agent(session)

    with agenttape.replay("runs/triage.tape") as session:
        answer = run_agent(session)     # identical trajectory, no live calls

Nothing about the agent's own logic is recorded or bypassed. Only the answers
that came from outside it are.
"""

from __future__ import annotations

import functools
import os
import platform
import random
import secrets
import sys
import time
import uuid as _uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from ._version import __version__
from .canonical import fingerprint, short_hash
from .channel import UNSET, Recorder, Replayer
from .clock import DeterministicClock
from .entropy import DeterministicRandom
from .errors import CompensationError, SessionStateError, TapeExhaustedError
from .events import Event, EventKind, rebuild_error
from .tape import Tape

__all__ = ["Session", "record", "replay"]

PathLike = Union[str, os.PathLike]

#: Type of a compensation plan: a registered name, or a name plus a payload.
Compensation = Optional[Union[str, Tuple[str, Dict[str, Any]]]]


class Session:
    """A recorded (or replayed) agent run.

    Construct with :meth:`record` or :meth:`replay`, or the module-level
    :func:`record` / :func:`replay` helpers. A session is a context manager;
    leaving the ``with`` block finalises the tape.
    """

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #

    def __init__(
        self,
        tape: Tape,
        mode: str,
        *,
        seed: Optional[int] = None,
        strict: bool = True,
        on_divergence: str = "raise",
        lookahead: int = 256,
        require_full_consumption: bool = True,
        on_exhausted: str = "raise",
    ) -> None:
        self._tape = tape
        self._mode = mode
        self._seed = seed
        self._strict = strict
        self._on_divergence = on_divergence
        self._lookahead = lookahead
        self._require_full_consumption = require_full_consumption
        self._on_exhausted = on_exhausted

        self._recorder: Optional[Recorder] = None
        self._replayer: Optional[Replayer] = None
        self._clock: Optional[DeterministicClock] = None
        self._rng: Optional[DeterministicRandom] = None

        self._closed = False
        self._summary: Optional[Dict[str, Any]] = None
        self._last_event: Optional[Event] = None
        self._outcome: Any = None
        self._outcome_fingerprint: Optional[str] = None
        self._state_fingerprints: List[str] = []
        self._compensators: Dict[str, Callable[..., Any]] = {}
        self._effects: List[Dict[str, Any]] = []
        self._rollback_results: List[Dict[str, Any]] = []
        self.forked = False
        self.fork_seq: Optional[int] = None

    # -- constructors --------------------------------------------------- #

    @classmethod
    def record(
        cls,
        path: PathLike,
        *,
        seed: Optional[int] = None,
        tags: Optional[List[str]] = None,
        meta: Optional[Dict[str, Any]] = None,
        overwrite: bool = False,
        blob_threshold: int = Tape.DEFAULT_BLOB_THRESHOLD,
        tape_id: Optional[str] = None,
    ) -> "Session":
        """Start recording a run to a new tape at *path*.

        Args:
            path: Directory for the tape. Created if missing.
            seed: Seed for the deterministic RNG. A fresh random seed is chosen
                if omitted, and is recorded so replay uses the same one.
            tags: Labels for filtering runs later.
            meta: Arbitrary metadata stored in the manifest.
            overwrite: Replace an existing tape at *path*.
            blob_threshold: Payload size above which content is stored as a blob.
            tape_id: Stable identifier; defaults to the directory name.
        """
        if seed is None:
            seed = random.SystemRandom().getrandbits(64)
        tape = Tape.create(
            path,
            tape_id=tape_id,
            seed=seed,
            tags=tags,
            meta=meta,
            blob_threshold=blob_threshold,
            overwrite=overwrite,
        )
        session = cls(tape, "record", seed=seed)
        session._recorder = Recorder(tape)
        session._write_header()
        return session

    @classmethod
    def replay(
        cls,
        path: PathLike,
        *,
        strict: bool = True,
        on_divergence: str = "raise",
        lookahead: int = 256,
        require_full_consumption: bool = True,
        on_exhausted: str = "raise",
        fork_to: Optional[PathLike] = None,
    ) -> "Session":
        """Replay a recorded run from the tape at *path*.

        Args:
            strict: Match positionally (the default, and the only mode with a
                strong correctness guarantee). ``False`` falls back to searching
                the tape for a matching request.
            on_divergence: ``"raise"`` (default) to fail loudly, ``"warn"`` to
                record the divergence and attempt to continue by searching.
            lookahead: Maximum events buffered while searching in non-strict mode.
            require_full_consumption: Raise from :meth:`close` if recorded events
                went unused. Leave this on: a silently truncated replay looks
                like success.
            on_exhausted: ``"raise"`` (default), or ``"live"`` to continue
                executing for real once the recording runs out. ``"live"``
                requires *fork_to*.
            fork_to: Destination tape for a counterfactual run. The recording is
                forked into it and the live tail is appended there, leaving the
                original tape untouched.

        Raises:
            ValueError: If ``on_exhausted="live"`` without ``fork_to``.
        """
        if on_exhausted not in ("raise", "live"):
            raise ValueError(
                "on_exhausted must be 'raise' or 'live', got {!r}".format(on_exhausted)
            )
        if on_exhausted == "live":
            if fork_to is None:
                raise ValueError(
                    "on_exhausted='live' needs fork_to=<path>: the events produced "
                    "after the recording ends have to be written somewhere, and "
                    "overwriting the original tape would destroy the recording."
                )
            tape = Tape.fork(path, fork_to, overwrite=False)
        else:
            tape = Tape.open(path, writable=False)

        session = cls(
            tape,
            "replay",
            seed=tape.manifest.get("seed"),
            strict=strict,
            on_divergence=on_divergence,
            lookahead=lookahead,
            require_full_consumption=require_full_consumption,
            on_exhausted=on_exhausted,
        )
        session._replayer = Replayer(
            tape, strict=strict, lookahead=lookahead, on_divergence=on_divergence
        )
        return session

    # ------------------------------------------------------------------ #
    # Identity / introspection
    # ------------------------------------------------------------------ #

    @property
    def tape(self) -> Tape:
        """The underlying :class:`~agenttape.Tape`."""
        return self._tape

    @property
    def mode(self) -> str:
        """``"record"`` or ``"replay"``. Becomes ``"record"`` after a live tail."""
        return self._mode

    @property
    def path(self) -> Path:
        """Directory of the tape."""
        return self._tape.path

    @property
    def seed(self) -> Optional[int]:
        """Seed backing :attr:`rng`."""
        return self._seed

    @property
    def last_event(self) -> Optional[Event]:
        """The most recent event handled by this session."""
        return self._last_event

    @property
    def divergences(self) -> List[Dict[str, Any]]:
        """Divergences observed so far (populated in ``on_divergence="warn"`` mode)."""
        return list(self._replayer.divergences) if self._replayer else []

    @property
    def consumed(self) -> int:
        """Number of recorded events served during replay."""
        return self._replayer.consumed if self._replayer else 0

    @property
    def remaining(self) -> int:
        """Recorded events not yet served during replay."""
        return len(self._replayer.unconsumed) if self._replayer else 0

    @property
    def counts(self) -> Dict[str, int]:
        """Event counts by kind, for recording sessions."""
        return dict(self._recorder.counts) if self._recorder else {}

    @property
    def verified(self) -> bool:
        """``True`` if the replay matched the recording exactly so far.

        Meaningful only in replay mode. A live tail makes the run a
        counterfactual by definition, so it is never reported as verified.
        """
        if self._mode != "replay" or self._replayer is None:
            return False
        if self.forked or self._replayer.divergences:
            return False
        return self._replayer.consumed > 0

    @property
    def outcome_value(self) -> Any:
        """The value passed to :meth:`outcome`, if any."""
        return self._outcome

    @property
    def outcome_fingerprint(self) -> Optional[str]:
        """Fingerprint of the value passed to :meth:`outcome`, if any.

        Populated in both modes, which is what lets
        :func:`agenttape.check_determinism` compare runs cheaply.
        """
        return self._outcome_fingerprint

    @property
    def state_fingerprints(self) -> List[str]:
        """Fingerprints recorded by :meth:`state`, in order."""
        return list(self._state_fingerprints)

    # -- lazily built helpers ------------------------------------------- #

    @property
    def clock(self) -> DeterministicClock:
        """The tape-backed clock."""
        if self._clock is None:
            self._clock = DeterministicClock(self)
        return self._clock

    @property
    def rng(self) -> DeterministicRandom:
        """The tape-backed random number generator."""
        if self._rng is None:
            self._rng = DeterministicRandom(self, self._seed)
        return self._rng

    # -- the active channel --------------------------------------------- #
    #
    # Exactly one of the two channels exists at a time, and which one changes
    # mid-session when a live tail begins. These accessors make the invariant
    # explicit and turn "impossible" states into a diagnosable error rather
    # than an AttributeError on None.

    def _require_recorder(self) -> Recorder:
        """The active recorder, or raise if this session is replaying.

        The raise is unreachable: ``_mode`` and a live recorder are set and
        cleared together. It exists so that a future refactor which breaks that
        pairing produces a diagnosable message instead of an ``AttributeError``
        on ``None``.
        """
        if self._recorder is None:  # pragma: no cover - defensive
            raise SessionStateError(
                "this session has no recorder: it was opened with "
                "Session.replay() and has not gone live"
            )
        return self._recorder

    def _require_replayer(self) -> Replayer:
        """The active replayer, or raise if this session is recording.

        Unreachable for the same reason as :meth:`_require_recorder`.
        """
        if self._replayer is None:  # pragma: no cover - defensive
            raise SessionStateError(
                "this session has no replayer: it was opened with Session.record()"
            )
        return self._replayer

    # ------------------------------------------------------------------ #
    # The core exchange
    # ------------------------------------------------------------------ #

    def exchange(
        self,
        kind: str,
        name: str,
        request: Any,
        *,
        fn: Optional[Callable[[], Any]] = None,
        response: Any = UNSET,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Record or replay one boundary crossing.

        This is the single primitive every convenience method is built on, and
        it is public so that boundaries the library does not model can still be
        captured.

        While recording, ``fn`` is executed and its return value (or raised
        exception) is written to the tape. While replaying, ``fn`` is **never
        called**: the recorded answer is returned, or the recorded exception is
        re-raised.

        Args:
            kind: One of :class:`~agenttape.events.EventKind`.
            name: Stable name of the boundary, e.g. ``"gpt-4o"`` or ``"search"``.
            request: Everything that identifies the call. This is fingerprinted
                and used for matching, so it must be canonicalisable and must
                contain every input the answer depends on.
            fn: Zero-argument callable that performs the real work.
            response: Pre-computed answer, for when the value is already in hand.
            meta: Extra fields stored on the event but excluded from matching.

        Returns:
            The answer, live or recorded.

        Raises:
            DivergenceError: While replaying, if the call does not match the tape.
            CanonicalizationError: If *request* has no deterministic encoding.
        """
        if self._closed:
            raise SessionStateError("this session is closed")

        key = fingerprint(kind, name, request)

        if self._mode == "record":
            recorder = self._require_recorder()
            value = recorder.exchange(kind, name, key, request, fn=fn, response=response, meta=meta)
            self._last_event = recorder.last_event
            return value

        try:
            event = self._require_replayer().next_event(kind, name, key, request)
        except TapeExhaustedError:
            if self._on_exhausted != "live":
                raise
            self._go_live()
            recorder = self._require_recorder()
            value = recorder.exchange(kind, name, key, request, fn=fn, response=response, meta=meta)
            self._last_event = recorder.last_event
            return value

        self._last_event = event
        if event.status == "error":
            raise rebuild_error(event.error or {})
        return event.response

    # ------------------------------------------------------------------ #
    # Convenience boundaries
    # ------------------------------------------------------------------ #

    def model(
        self,
        name: str,
        request: Any,
        *,
        fn: Optional[Callable[[], Any]] = None,
        response: Any = UNSET,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Record or replay a model call.

        ``request`` should carry everything the answer depends on -- the full
        message list, the sampling parameters, and the model version. Replay
        cannot detect a change it was not told about.

        Example::

            answer = session.model(
                "gpt-4o-2024-11-20",
                {"messages": messages, "temperature": 0.0, "max_tokens": 512},
                fn=lambda: client.chat(messages),
            )
        """
        return self.exchange(EventKind.MODEL, name, request, fn=fn, response=response, meta=meta)

    def tool(
        self,
        name: str,
        args: Optional[Any] = None,
        *,
        kwargs: Optional[Dict[str, Any]] = None,
        fn: Optional[Callable[[], Any]] = None,
        response: Any = UNSET,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Record or replay a tool call.

        The request is ``{"args": [...], "kwargs": {...}}``, which is what makes
        replay able to report *which* argument changed when a call diverges.

        Example::

            hits = session.tool("search", [query], kwargs={"k": 5},
                                fn=lambda: search(query, k=5))
        """
        request = {"args": list(args) if args is not None else [], "kwargs": dict(kwargs or {})}
        return self.exchange(EventKind.TOOL, name, request, fn=fn, response=response, meta=meta)

    def call(
        self,
        kind: str,
        name: str,
        request: Any,
        *,
        fn: Optional[Callable[[], Any]] = None,
        response: Any = UNSET,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Alias for :meth:`exchange`."""
        return self.exchange(kind, name, request, fn=fn, response=response, meta=meta)

    # -- decorators ------------------------------------------------------ #

    def wrap(self, kind: str, name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator turning a function into a recorded boundary.

        The recorded request is ``{"args": [...], "kwargs": {...}}``, so every
        argument must be canonically encodable.

        Example::

            @session.wrap(agenttape.EventKind.TOOL, "search")
            def search(query, k=5):
                return index.query(query, k)

            hits = search("refund policy")     # recorded, or served from tape
        """

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            @functools.wraps(fn)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                request = {"args": list(args), "kwargs": kwargs}
                return self.exchange(kind, name, request, fn=lambda: fn(*args, **kwargs))

            wrapper.__agenttape_wrapped__ = True  # type: ignore[attr-defined]
            return wrapper

        return decorator

    def model_fn(self, name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator: :meth:`wrap` with :data:`~agenttape.events.EventKind.MODEL`."""
        return self.wrap(EventKind.MODEL, name)

    def tool_fn(self, name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator: :meth:`wrap` with :data:`~agenttape.events.EventKind.TOOL`."""
        return self.wrap(EventKind.TOOL, name)

    # ------------------------------------------------------------------ #
    # Environment
    # ------------------------------------------------------------------ #

    def env(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Read an environment variable, recording the value seen."""
        return self.exchange(
            EventKind.ENV,
            "env:" + key,
            {"key": key, "default": default},
            fn=lambda: os.environ.get(key, default),
        )

    def read_text(self, path: PathLike, encoding: str = "utf-8") -> str:
        """Read a text file, recording its contents.

        Replay returns the recorded contents even if the file has since changed
        or been deleted -- which is the point, and also a caveat: a large file
        read this way lands on the tape.
        """
        target = os.fspath(path)
        return self.exchange(
            EventKind.ENV,
            "file:" + target,
            {"path": target, "encoding": encoding, "mode": "text"},
            fn=lambda: Path(target).read_text(encoding=encoding),
        )

    def read_bytes(self, path: PathLike) -> bytes:
        """Read a binary file, recording its contents (as a blob if large)."""
        target = os.fspath(path)
        return self.exchange(
            EventKind.ENV,
            "file:" + target,
            {"path": target, "mode": "bytes"},
            fn=lambda: Path(target).read_bytes(),
        )

    def uuid4(self) -> _uuid.UUID:
        """Generate a UUID, recording it so replay yields the same value."""
        return self.exchange(EventKind.ENV, "uuid4", {}, fn=_uuid.uuid4)

    def token_hex(self, nbytes: int = 16) -> str:
        """Generate a random token, recorded for replay."""
        return self.exchange(
            EventKind.ENV, "token_hex", {"nbytes": nbytes}, fn=lambda: secrets.token_hex(nbytes)
        )

    # ------------------------------------------------------------------ #
    # Annotations and state
    # ------------------------------------------------------------------ #

    def mark(self, label: str, **data: Any) -> None:
        """Annotate the tape at this point in the run.

        Marks are recorded and matched like any other event, so replay verifies
        that the run reached the same points.
        """
        self.exchange(EventKind.MARK, label, {"label": label, "data": data}, response=None)

    def log(self, message: str, **data: Any) -> None:
        """Write a free-form note into the tape."""
        self.exchange(EventKind.LOG, "log", {"message": message, "data": data}, response=message)

    def state(self, snapshot: Any, *, label: Optional[str] = None) -> str:
        """Record a state snapshot and return its fingerprint.

        The snapshot itself is stored on the tape (as a blob if large), which is
        what makes checkpoint replay possible: you can inspect, or fork from,
        the state at any recorded point.

        The fingerprint is derived from the snapshot and is part of the request,
        so a run whose state differs at this point diverges here rather than
        several steps later.
        """
        name = label or "state"
        state_fingerprint = fingerprint(EventKind.STATE, name, snapshot)
        self.exchange(
            EventKind.STATE,
            name,
            {"fingerprint": state_fingerprint, "label": label},
            response=snapshot,
        )
        self._state_fingerprints.append(state_fingerprint)
        return state_fingerprint

    def outcome(self, value: Any) -> Any:
        """Record the run's final result.

        :func:`agenttape.check_determinism` compares recorded outcomes across
        repeated replays, so this is the cheapest way to make a run's result
        machine-comparable.

        *value* must be canonically encodable.
        """
        outcome_fingerprint = fingerprint(EventKind.OUTCOME, "outcome", value)
        self.exchange(
            EventKind.OUTCOME,
            "outcome",
            {"fingerprint": outcome_fingerprint},
            response=value,
        )
        self._outcome = value
        self._outcome_fingerprint = outcome_fingerprint
        return value

    # ------------------------------------------------------------------ #
    # Effects and rollback
    # ------------------------------------------------------------------ #

    def compensator(self, name: str, fn: Optional[Callable[..., Any]] = None):
        """Register a compensating action under *name*.

        Compensators are addressed by name rather than by reference because the
        tape must be replayable in a process where the original callable does
        not exist. A name in the tape plus a registration in the replaying
        process is the only portable arrangement.

        The compensator is called as ``fn(payload, result)``, where ``payload``
        is the dict supplied to :meth:`effect` and ``result`` is what the effect
        returned.

        Usable as a decorator::

            @session.compensator("refund")
            def refund(payload, result):
                return payments.refund(result["charge_id"])
        """
        if fn is None:

            def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
                self._compensators[name] = func
                return func

            return decorator
        self._compensators[name] = fn
        return fn

    def effect(
        self,
        name: str,
        fn: Optional[Callable[[], Any]] = None,
        *,
        args: Tuple[Any, ...] = (),
        kwargs: Optional[Dict[str, Any]] = None,
        compensate: Compensation = None,
        response: Any = UNSET,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Perform a side-effecting action, recording how to undo it.

        The compensation plan lives *in the tape*, not in the caller's head.
        That is the design point: a rollback that is derived from the recording
        can be replayed, audited, and compared, whereas an improvised rollback
        is a second unreproducible run.

        Args:
            name: Name of the effect, e.g. ``"charge_card"``.
            fn: Callable performing the effect.
            args: Positional arguments for *fn*; recorded for matching.
            kwargs: Keyword arguments for *fn*; recorded for matching.
            compensate: ``None`` for an irreversible effect, a registered
                compensator name, or ``(name, payload_dict)``.
            response: Pre-computed result, for effects already performed.
            meta: Extra fields stored on the event.

        Returns:
            The effect's result.
        """
        kwargs = dict(kwargs or {})
        spec = _compensation_spec(compensate)
        request = {"args": list(args), "kwargs": kwargs, "compensate": spec}
        result = self.exchange(
            EventKind.EFFECT,
            name,
            request,
            fn=(lambda: fn(*args, **kwargs)) if fn is not None else None,
            response=response,
            meta=meta,
        )
        if self._last_event is not None:
            # Every effect is tracked, including irreversible ones, so that
            # `session.effects` is a faithful audit of what the run did. Only
            # those with a plan are ever compensated.
            self._effects.append(
                {
                    "seq": self._last_event.seq,
                    "name": name,
                    "compensator": spec["name"] if spec else None,
                    "payload": (spec or {}).get("payload") or {},
                    "result": result,
                    "compensated": False,
                }
            )
        return result

    def rollback(
        self, *, upto: Optional[int] = None, dry_run: bool = False
    ) -> List[Dict[str, Any]]:
        """Apply compensations for recorded effects, newest first.

        Each compensation is itself an event, so a rollback is replayable and
        auditable. Compensating an already-compensated effect is skipped, which
        is what makes a retried rollback safe.

        Args:
            upto: Only compensate effects with ``seq >= upto``. Use this to roll
                back to a recorded checkpoint; ``None`` compensates everything
                outstanding.
            dry_run: Return the plan without applying it.

        Returns:
            One record per compensation attempted, with ``ok`` and either
            ``result`` or ``error``.
        """
        plan = [
            entry
            for entry in reversed(self._effects)
            if entry["compensator"] is not None
            and not entry["compensated"]
            and (upto is None or entry["seq"] >= upto)
        ]
        if dry_run:
            return [
                {
                    "effect_seq": entry["seq"],
                    "name": entry["name"],
                    "compensator": entry["compensator"],
                }
                for entry in plan
            ]

        results: List[Dict[str, Any]] = []
        for entry in plan:
            compensator_name = entry["compensator"]
            payload = entry["payload"]
            effect_result = entry["result"]

            def thunk(
                compensator_name: str = compensator_name,
                payload: Dict[str, Any] = payload,
                effect_result: Any = effect_result,
            ) -> Any:
                fn = self._compensators.get(compensator_name)
                if fn is None:
                    raise CompensationError(
                        "no compensator registered for {!r}; register it with "
                        "session.compensator({!r}, fn)".format(compensator_name, compensator_name)
                    )
                return fn(payload, effect_result)

            try:
                value = self.exchange(
                    EventKind.COMPENSATE,
                    compensator_name,
                    {"effect_seq": entry["seq"], "payload": payload},
                    fn=thunk,
                )
            except Exception as exc:
                results.append(
                    {
                        "effect_seq": entry["seq"],
                        "compensator": compensator_name,
                        "ok": False,
                        "error": "{}: {}".format(type(exc).__name__, exc),
                    }
                )
                continue

            entry["compensated"] = True
            results.append(
                {
                    "effect_seq": entry["seq"],
                    "compensator": compensator_name,
                    "ok": True,
                    "result": value,
                }
            )

        self._rollback_results.extend(results)
        return results

    @property
    def effects(self) -> List[Dict[str, Any]]:
        """Effects recorded so far, with their compensation status."""
        return [
            {
                "seq": entry["seq"],
                "name": entry["name"],
                "compensator": entry["compensator"],
                "compensated": entry["compensated"],
            }
            for entry in self._effects
        ]

    # ------------------------------------------------------------------ #
    # Live tail
    # ------------------------------------------------------------------ #

    def _go_live(self) -> None:
        """Switch from replay to recording when the tape runs out.

        Only reachable when ``on_exhausted="live"``. The tape being written to
        is a fork, never the original, so the recording stays intact.
        """
        if self._tape is None or not self._tape.writable:
            raise SessionStateError(
                "cannot continue live: this session has no writable tape. "
                "Use Session.replay(..., on_exhausted='live', fork_to=<path>)."
            )
        self.forked = True
        self.fork_seq = self._replayer.consumed if self._replayer else None
        self._mode = "record"
        self._recorder = Recorder(self._tape)
        # Recorded directly rather than through exchange(): the agent did not
        # make this call, so replay must not expect to match it.
        self._tape.append(
            kind=EventKind.FORK,
            name="fork",
            key=fingerprint(EventKind.FORK, "fork", {"at": self.fork_seq}),
            request={"at": self.fork_seq},
            response={
                "forked_at": self.fork_seq,
                "note": "the recording ended here; everything after this event is counterfactual",
                "parent": self._tape.manifest.get("parent"),
            },
            meta={"written_by": "Session._go_live"},
        )

    # ------------------------------------------------------------------ #
    # Header and lifecycle
    # ------------------------------------------------------------------ #

    def _write_header(self) -> None:
        """Write the ``record`` event describing the environment of the run."""
        info = {
            "agenttape_version": __version__,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "seed": self._seed,
            "cwd": os.getcwd(),
            "argv": list(sys.argv),
            "tape_id": self._tape.manifest.get("tape_id"),
        }
        self._tape.append(
            kind=EventKind.RECORD,
            name="record",
            key=fingerprint(EventKind.RECORD, "record", info),
            request={},
            response=info,
            ts=time.time(),
        )

    def close(self, *, check: Optional[bool] = None) -> Dict[str, Any]:
        """Finalise the run and return a summary.

        While recording, this writes the tape footer and manifest. While
        replaying, it verifies that every recorded event was consumed.

        Idempotent. Safe to call from a ``finally`` block.

        Raises:
            UnconsumedEventsError: In replay mode, if the run finished early.
        """
        if self._closed:
            return self._summary or {}

        # Annotated because the two branches below build different shapes, and
        # without this the inferred type of the first one constrains the second.
        summary: Dict[str, Any]

        if self._mode == "record":
            recorder = self._require_recorder()
            summary = {
                # A session that went live after its recording ran out is not a
                # plain recording. Labelling it here is the only place that can
                # happen: going live flips _mode to "record", so the replay
                # branch below is never reached for a forked session.
                "mode": "counterfactual" if self.forked else "record",
                "tape": str(self._tape.path),
                "events": self._tape.event_count,
                "counts": dict(recorder.counts),
                "errors": recorder.errors,
                "outcome": self._outcome_fingerprint,
                "states": list(self._state_fingerprints),
                "effects": len(self._effects),
                "compensated": sum(1 for e in self._effects if e["compensated"]),
                "duration_seconds": round(recorder.total_duration, 6),
                "seed": self._seed,
            }
            if self.forked:
                summary["forked_at"] = self.fork_seq
            self._tape.close(summary=summary)
            summary["digest"] = self._tape.digest
        else:
            replayer = self._require_replayer()
            should_check = (
                self._require_full_consumption and not self.forked if check is None else check
            )
            # Always finish the stream: it both makes `remaining` meaningful and
            # releases the event log's file handle.
            replayer.assert_exhausted(require_full=bool(should_check))
            summary = {
                "mode": "replay",
                "tape": str(self._tape.path),
                "consumed": replayer.consumed,
                "remaining": len(replayer.unconsumed),
                "divergences": list(replayer.divergences),
                "forked": self.forked,
                "fork_seq": self.fork_seq,
                "verified": self.verified and not self.forked,
                "digest": self._tape.digest,
            }
            # Release the event stream's file handle. On Windows a live handle
            # blocks the tape directory from being moved or deleted.
            replayer.close()

        self._closed = True
        self._summary = summary
        return summary

    @property
    def closed(self) -> bool:
        """``True`` once :meth:`close` has run."""
        return self._closed

    @property
    def summary(self) -> Dict[str, Any]:
        """Summary from :meth:`close`, or ``{}`` if the session is still open."""
        return self._summary or {}

    def __enter__(self) -> "Session":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # An exception from the body must not be masked by a completion check.
        self.close(check=False if exc_type is not None else None)

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return "<Session mode={} tape={} digest={}>".format(
            self._mode, self._tape.path.name, short_hash(self._tape.digest)
        )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _compensation_spec(compensate: Compensation) -> Optional[Dict[str, Any]]:
    """Normalise a ``compensate=`` argument into a storable spec."""
    if compensate is None:
        return None
    if isinstance(compensate, str):
        return {"name": compensate, "payload": {}}
    if isinstance(compensate, tuple) and len(compensate) == 2 and isinstance(compensate[0], str):
        payload = compensate[1] or {}
        if not isinstance(payload, dict):
            raise CompensationError(
                "the payload in compensate=({!r}, ...) must be a dict, got {}".format(
                    compensate[0], type(payload).__name__
                )
            )
        return {"name": compensate[0], "payload": payload}
    raise CompensationError(
        "compensate= must be None, a registered compensator name, or a "
        "(name, payload_dict) tuple; got {!r}".format(compensate)
    )


# --------------------------------------------------------------------------- #
# Module-level helpers
# --------------------------------------------------------------------------- #


def record(path: PathLike, **kwargs: Any) -> Session:
    """Start recording to *path*. See :meth:`Session.record`."""
    return Session.record(path, **kwargs)


def replay(path: PathLike, **kwargs: Any) -> Session:
    """Replay from *path*. See :meth:`Session.replay`."""
    return Session.replay(path, **kwargs)
