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
