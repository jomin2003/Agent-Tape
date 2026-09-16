"""Deterministic randomness.

An agent that samples an action, shuffles a candidate list, or jitters a retry
delay is non-deterministic in a way that has nothing to do with the model.

Rather than merely *seeding* the generator and hoping the agent makes the same
number of draws in the same order, :class:`DeterministicRandom` records every
draw. That is a stronger guarantee: even if the agent's control flow changes,
the recorded entropy is what replay returns, so a divergence shows up as a
mismatch in the *call sequence* (loudly) rather than as silently different
values.

Implementation note
-------------------

``random.Random`` implements every public method (``randint``, ``choice``,
``shuffle``, ``sample``, ``gauss``, ...) on top of exactly two primitives:
``random()`` and ``getrandbits()``. Overriding those two captures the whole
surface area, including methods that do not exist yet.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Any, Optional

from .events import EventKind

if TYPE_CHECKING:  # pragma: no cover
    from .session import Session

__all__ = ["DeterministicRandom"]


class DeterministicRandom(random.Random):
    """A :class:`random.Random` whose entropy comes from the tape.

    Obtain one from :attr:`agenttape.Session.rng`; do not construct directly.

    The generator is seeded from the tape's recorded seed *and* records each
    draw, so it is reproducible in two independent ways. Seeding alone would be
    enough if the agent's draw sequence never changed; recording makes the
    guarantee robust to code changes, which is what you actually need when
    debugging.
    """

    #: The session this generator draws its entropy from.
    _agenttape_session: "Session"

    def __new__(cls, *args: Any, **kwargs: Any) -> "DeterministicRandom":
        """Construct without forwarding arguments to ``random.Random.__new__``.

        ``random.Random`` is a C type, and before Python 3.11 its ``__new__``
        inspects the positional argument tuple itself::

            if (PyTuple_GET_SIZE(args) > 1) {
                PyErr_SetString(PyExc_TypeError, "Random() requires 0 or 1 argument");
                return NULL;
            }
            if (PyTuple_GET_SIZE(args) == 1)
                arg = PyTuple_GET_ITEM(args, 0);
            tmp = random_seed(self, arg);

        (Modules/_randommodule.c, CPython 3.10.)

        So ``DeterministicRandom(session, seed)`` failed inside ``tp_new`` -- with
        two arguments the call is rejected outright, and with one it would try to
        *seed with the session object*. ``__init__`` was never reached, which is
        why the traceback pointed at the constructor call site rather than at
        anything inside it.

        The argument check was removed in 3.11, so a modern interpreter accepts
        the call and the bug is invisible. Dropping the arguments here makes
        construction valid on every version: zero positional arguments is the one
        case ``random_new`` has always accepted.

        Note the deliberate side effect: with no argument, ``random_new`` seeds
        from the operating system. That costs one urandom read per generator and
        is immediately overwritten by :meth:`__init__`, so it cannot affect
        determinism.
        """
        return super().__new__(cls)

    def __init__(self, session: "Session", seed: Optional[int] = None) -> None:
        # The channel must be in place before seeding, because seeding must not
        # touch a missing attribute.
        self._agenttape_session = session
        # gauss() reads this attribute directly and random.Random.__init__ is
        # what normally initialises it. We deliberately do not call that (it
        # takes a seed as its only argument), so set it here. Without this,
        # session.rng.gauss() raises AttributeError on Python 3.11+.
        self.gauss_next: Optional[float] = None
        # seed() is the documented entry point and exists on every supported
        # version, unlike random.Random.__init__ which only appears in 3.11+.
        self.seed(seed)

    # -- the two primitives everything else is built on --------------------- #

    def random(self) -> float:
        """Uniform float in ``[0.0, 1.0)``, as recorded."""
        return self._agenttape_session.exchange(
            EventKind.RANDOM,
            "random.random",
            {},
            fn=lambda: random.Random.random(self),
        )

    def getrandbits(self, k: int) -> int:
        """*k* random bits, as recorded."""
        return self._agenttape_session.exchange(
            EventKind.RANDOM,
            "random.getrandbits",
            {"k": k},
            fn=lambda: random.Random.getrandbits(self, k),
        )

    # -- explicit state control --------------------------------------------- #

    def reseed(self, seed: Optional[int] = None) -> None:
        """Reseed the underlying generator.

        Recorded as an event so that replay reseeds at the same point. Prefer
        this over calling :meth:`random.Random.seed` directly, which would
        desynchronise record and replay.
        """
        self._agenttape_session.exchange(
            EventKind.RANDOM,
            "random.seed",
            {"seed": seed},
            fn=lambda: random.Random.seed(self, seed),
            response=None,
        )

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return "<DeterministicRandom seed={!r}>".format(getattr(self, "_seed", None))
