"""Example 04 -- proving a recording is actually reproducible.

`check_determinism` is the self-test for a tape. It replays several times and
confirms that every pass consumes the whole tape, reaches the same outcome, and
produces the same state fingerprints.

The example contrasts two agents: one that routes every source of non-determinism
through the session, and one that reads raw entropy behind the library's back. The
second one is the important one -- it is what a leak looks like, and the check
finds it.

Run it with:

    PYTHONPATH=src python examples/04_determinism_check.py
"""

from __future__ import annotations

import random
import shutil
import tempfile
from pathlib import Path

import agenttape


def well_behaved_agent(session, question="Is this urgent?"):
    """Every non-deterministic read goes through the session."""
    session.mark("start")

    # Recorded clock.
    started_at = session.clock.isoformat()

    # Recorded entropy.
    temperature = session.rng.uniform(0.0, 1.0)
    plan = session.rng.choice(["fast-path", "deep-path"])

    answer = session.model(
        "mock-llm-1",
        {"messages": [{"role": "user", "content": question}],
         "temperature": round(temperature, 4)},
        fn=lambda: "{}: urgent".format(plan),
    )

    session.state({"plan": plan, "started_at": started_at}, label="after-model")
    session.outcome({"plan": plan, "answer": answer})
    return answer


def leaky_agent(session, question="Is this urgent?"):
    """Reads raw entropy that the session never sees.

    This is not a contrived mistake -- it is what a helper library that calls
    `random` or `time` directly looks like from the outside. The session cannot
    record what it was not asked to do.
    """
    jitter = random.random()                       # <-- not recorded
    answer = session.model(
        "mock-llm-1",
        {"messages": [{"role": "user", "content": question}], "temperature": 0.0},
        fn=lambda: "urgent" if jitter > 0.5 else "not urgent",
    )
    session.outcome({"answer": answer})
    return answer


def main() -> None:
    workspace = Path(tempfile.mkdtemp(prefix="agenttape-example-"))
    good_tape = str(workspace / "good.tape")
    leaky_tape = str(workspace / "leaky.tape")

    try:
        # ------------------------------------------------------------------ #
        print("== 1. an agent that routes everything through the session ==")
        with agenttape.record(good_tape) as session:
            well_behaved_agent(session)

        report = agenttape.check_determinism(good_tape, well_behaved_agent, repeats=3)
        print(report.render())
        assert report.deterministic
        print()

        # ------------------------------------------------------------------ #
        print("== 2. an agent that reads raw entropy ==")
        with agenttape.record(leaky_tape) as session:
            leaky_agent(session)

        report = agenttape.check_determinism(leaky_tape, leaky_agent, repeats=5)
        print(report.render())
        print()

        if report.deterministic:
            print("(this particular recording happened to replay cleanly; run it")
            print(" again and the leak will show up -- which is exactly the point:")
            print(" an unrecorded boundary makes failure probabilistic.)")
        else:
            print("The check found the leak. The fix is to route that read through")
            print(" the session -- not to loosen the check.")
        print()

        # ------------------------------------------------------------------ #
        print("== 3. the same two tapes, compared structurally ==")
        print("well-behaved agent event kinds: {}".format(
            sorted(agenttape.Tape.describe(good_tape)["counts"])))
        print("leaky agent event kinds:        {}".format(
            sorted(agenttape.Tape.describe(leaky_tape)["counts"])))
        print()
        print("The leaky tape has no 'random' events at all -- the recording simply")
        print("has no idea the value existed. That is what 'the completeness ceiling'")
        print("means in research/deterministic-replay-and-rollback-for-ai-agents.md.")

    finally:
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    main()
