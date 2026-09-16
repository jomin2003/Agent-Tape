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
from typing import TYPE_CHECKING, Optional

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

    def __init__(self, session: "Session", seed: Optional[int] = None) -> None:
        # The channel must be in place before seeding, because seeding must not
        # touch a missing attribute.
        self._agenttape_session = session
        # Call seed() rather than super().__init__().
        #
        # Before Python 3.11, random.Random does not define __init__ -- it is a
        # C type that seeds in __new__ -- so super().__init__(seed) resolves to
        # object.__init__ and raises "Random() requires 0 or 1 argument". seed()
        # exists on every supported version and is the documented entry point.
        # Found by CI on the 3.9 and 3.10 jobs; a local 3.13 venv never sees it.
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
