"""Deterministic clock.

An agent that embeds "Today is 12 April 2026" in a prompt, or that branches on
``datetime.now().hour``, is non-deterministic even when the model is not. The
clock is therefore a recorded boundary like any other: the value the agent saw
during recording is the value it sees during replay, forever.

The convenience methods all funnel through a single recorded primitive
(``time.time()`` or ``datetime.now(tz)``), so the tape stays small and the
replay behaviour is easy to reason about.

``sleep`` is special: during recording it actually sleeps (the recorded duration
is what really elapsed), but during replay it returns immediately. Replaying an
agent that waits on backoff timers should not take minutes.
"""

from __future__ import annotations

import datetime as _dt
import time
from typing import TYPE_CHECKING, Optional

from .events import EventKind

if TYPE_CHECKING:  # pragma: no cover
    from .session import Session

__all__ = ["DeterministicClock"]


class DeterministicClock:
    """A clock whose readings come from the tape.

    Obtain one from :attr:`agenttape.Session.clock`; do not construct directly.
    """

    def __init__(self, session: "Session") -> None:
        self._session = session

    # -- primitives --------------------------------------------------------- #

    def time(self) -> float:
        """Seconds since the Unix epoch, as recorded."""
        return self._session.exchange(EventKind.CLOCK, "time.time", {}, fn=time.time)

    def monotonic(self) -> float:
        """Monotonic seconds, as recorded. Not comparable across runs."""
        return self._session.exchange(EventKind.CLOCK, "time.monotonic", {}, fn=time.monotonic)

    # -- derived ------------------------------------------------------------ #

    def now(self, tz: Optional[_dt.tzinfo] = None) -> _dt.datetime:
        """Current datetime, as recorded.

        The full datetime (including UTC offset) is recorded, so replaying on a
        machine in a different timezone yields exactly what the original run
        saw.
        """
        request = {"tz": tz.tzname(None) if tz is not None else None}
        return self._session.exchange(
            EventKind.CLOCK,
            "datetime.now",
            request,
            fn=lambda: _dt.datetime.now(tz),
        )

    def utcnow(self) -> _dt.datetime:
        """Current UTC datetime, as recorded."""
        return self._session.exchange(
            EventKind.CLOCK,
            "datetime.utcnow",
            {},
            fn=lambda: _dt.datetime.now(_dt.timezone.utc),
        )

    def today(self, tz: Optional[_dt.tzinfo] = None) -> _dt.date:
        """Today's date, as recorded."""
        return self._session.exchange(
            EventKind.CLOCK,
            "date.today",
            {"tz": tz.tzname(None) if tz is not None else None},
            fn=lambda: _dt.datetime.now(tz).date(),
        )

    # -- time travel -------------------------------------------------------- #

    def sleep(self, seconds: float) -> None:
        """Sleep for *seconds* while recording; return immediately while replaying."""
        self._session.exchange(
            EventKind.CLOCK,
            "time.sleep",
            {"seconds": seconds},
            fn=lambda: time.sleep(seconds),
            response=None,
        )

    def isoformat(self, timespec: str = "seconds") -> str:
        """Current time as an ISO-8601 string, as recorded."""
        return self._session.exchange(
            EventKind.CLOCK,
            "datetime.isoformat",
            {"timespec": timespec},
            fn=lambda: _dt.datetime.now().isoformat(timespec=timespec),
        )

    def __call__(self) -> float:
        """``clock()`` is an alias for :meth:`time`."""
        return self.time()

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return "<DeterministicClock mode={!r}>".format(getattr(self._session, "mode", "?"))
