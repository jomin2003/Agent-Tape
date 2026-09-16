"""Recorded randomness."""

from __future__ import annotations

import agenttape as at


def test_random_is_reproduced(tape_path):
    with at.record(tape_path) as session:
        recorded = [session.rng.random() for _ in range(10)]
    with at.replay(tape_path) as session:
        assert [session.rng.random() for _ in range(10)] == recorded


def test_getrandbits_is_reproduced(tape_path):
    with at.record(tape_path) as session:
        recorded = [session.rng.getrandbits(32) for _ in range(5)]
    with at.replay(tape_path) as session:
        assert [session.rng.getrandbits(32) for _ in range(5)] == recorded


def test_choice_is_reproduced(tape_path):
    options = ["alpha", "beta", "gamma", "delta"]
    with at.record(tape_path) as session:
        recorded = [session.rng.choice(options) for _ in range(20)]
    with at.replay(tape_path) as session:
        assert [session.rng.choice(options) for _ in range(20)] == recorded


def test_shuffle_is_reproduced(tape_path):
    with at.record(tape_path) as session:
        items = list(range(10))
        session.rng.shuffle(items)
        recorded = list(items)
    with at.replay(tape_path) as session:
        items = list(range(10))
        session.rng.shuffle(items)
        assert list(items) == recorded


def test_sample_is_reproduced(tape_path):
    with at.record(tape_path) as session:
        recorded = session.rng.sample(range(100), 10)
    with at.replay(tape_path) as session:
        assert session.rng.sample(range(100), 10) == recorded


def test_randint_and_uniform_are_reproduced(tape_path):
    with at.record(tape_path) as session:
        recorded = (
            session.rng.randint(1, 100),
            session.rng.uniform(0.0, 1.0),
            session.rng.randrange(0, 1000, 7),
        )
    with at.replay(tape_path) as session:
        assert (
            session.rng.randint(1, 100),
            session.rng.uniform(0.0, 1.0),
            session.rng.randrange(0, 1000, 7),
        ) == recorded


def test_generator_is_seeded_from_the_recorded_seed(tape_path):
    with at.record(tape_path, seed=1234) as session:
        assert session.seed == 1234
        session.rng.random()
    assert at.Tape.describe(tape_path)["seed"] == 1234
    with at.replay(tape_path) as session:
        assert session.seed == 1234
        session.rng.random()


def test_gauss_is_reproduced(tape_path):
    """``gauss`` reads ``gauss_next`` directly rather than going through random().

    That attribute is normally initialised by ``random.Random.__init__``, which
    this subclass does not call, so it has to be set by hand. Without that,
    ``gauss()`` raises AttributeError on Python 3.11+.
    """
    with at.record(tape_path) as session:
        recorded = [session.rng.gauss(0.0, 1.0) for _ in range(10)]
    with at.replay(tape_path) as session:
        assert [session.rng.gauss(0.0, 1.0) for _ in range(10)] == recorded


def test_the_generator_can_be_constructed_with_its_full_signature(tape_path):
    """Construction must not forward arguments to ``random.Random.__new__``.

    Before Python 3.11 that C-level ``__new__`` accepts at most one positional
    argument, so ``DeterministicRandom(session, seed)`` raised
    ``TypeError: Random() requires 0 or 1 argument`` before ``__init__`` ran.
    This asserts the constructor accepts both arguments on every version.
    """
    from agenttape.entropy import DeterministicRandom

    with at.record(tape_path) as session:
        generator = DeterministicRandom(session, 42)
        assert generator.random() == DeterministicRandom(session, 42).random()


def test_other_random_methods_still_work(tape_path):
    """A sweep over the inherited surface, which is the point of subclassing.

    Every one of these bottoms out in ``random()`` or ``getrandbits()``, so
    overriding those two is enough to capture the whole public API -- including
    methods added to ``random.Random`` after this library was written.
    """

    def draw(rng):
        return {
            "triangular": rng.triangular(0.0, 1.0, 0.5),
            "betavariate": rng.betavariate(2.0, 2.0),
            "expovariate": rng.expovariate(1.0),
            "gammavariate": rng.gammavariate(2.0, 1.0),
            "lognormvariate": rng.lognormvariate(0.0, 1.0),
            "normalvariate": rng.normalvariate(0.0, 1.0),
            "paretovariate": rng.paretovariate(1.0),
            "weibullvariate": rng.weibullvariate(1.0, 1.0),
            "randbytes": rng.randbytes(8),
        }

    with at.record(tape_path) as session:
        recorded = draw(session.rng)
    with at.replay(tape_path) as session:
        assert draw(session.rng) == recorded


def test_reseed_is_recorded(tape_path):
    with at.record(tape_path) as session:
        session.rng.reseed(7)
        recorded = [session.rng.random() for _ in range(3)]
    with at.replay(tape_path) as session:
        session.rng.reseed(7)
        assert [session.rng.random() for _ in range(3)] == recorded


def test_draws_are_recorded_as_random_events(tape_path):
    with at.record(tape_path) as session:
        session.rng.random()
        session.rng.getrandbits(8)
    assert at.Tape.describe(tape_path)["counts"]["random"] == 2


def test_recording_the_same_seed_twice_gives_the_same_draws(tmp_path):
    """Seeding alone is reproducible; recording makes it robust to code changes."""
    first = str(tmp_path / "a.tape")
    second = str(tmp_path / "b.tape")
    for path in (first, second):
        with at.record(path, seed=99) as session:
            session.rng.random()

    def draw(session):
        return session.rng.random()

    with at.replay(first) as session:
        value_a = draw(session)
    with at.replay(second) as session:
        value_b = draw(session)
    assert value_a == value_b
