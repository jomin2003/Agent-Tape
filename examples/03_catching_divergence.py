"""Example 03 -- using replay as a regression test.

This is the most underrated use of deterministic replay. You record a golden run,
change the agent, replay the tape, and find out *exactly* where behaviour moved --
instead of discovering it when a user reports it.

The example deliberately breaks the agent in three different ways and shows the
three failure modes replay can report.

Run it with:

    PYTHONPATH=src python examples/03_catching_divergence.py
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import agenttape
from agenttape import DivergenceError


def call_model(messages, temperature):
    return "Escalate to tier 2 if the customer is on the enterprise plan."


def classify(text):
    return "enterprise" if "enterprise" in text else "standard"


# --------------------------------------------------------------------------- #
# The golden agent.
# --------------------------------------------------------------------------- #


def golden_agent(session, question="Who handles enterprise escalations?"):
    tier = session.tool("classify", [question], fn=lambda: classify(question))
    answer = session.model(
        "mock-llm-1",
        {"messages": [{"role": "user", "content": question}], "temperature": 0.0},
        fn=lambda: call_model([{"role": "user", "content": question}], 0.0),
    )
    session.outcome({"tier": tier, "answer": answer})
    return answer


# --------------------------------------------------------------------------- #
# Three ways to break it.
# --------------------------------------------------------------------------- #


def changed_prompt(session, question="Who handles enterprise escalations?"):
    """A prompt edit: the same shape, different words."""
    tier = session.tool("classify", [question], fn=lambda: classify(question))
    answer = session.model(
        "mock-llm-1",
        {
            "messages": [
                {"role": "system", "content": "Be concise."},  # <-- new
                {"role": "user", "content": question},
            ],
            "temperature": 0.0,
        },
        fn=lambda: call_model([], 0.0),
    )
    session.outcome({"tier": tier, "answer": answer})
    return answer


def reordered_calls(session, question="Who handles enterprise escalations?"):
    """An 'innocent' refactor that moved an independent call earlier."""
    answer = session.model(
        "mock-llm-1",
        {"messages": [{"role": "user", "content": question}], "temperature": 0.0},
        fn=lambda: call_model([], 0.0),
    )
    tier = session.tool("classify", [question], fn=lambda: classify(question))
    session.outcome({"tier": tier, "answer": answer})
    return answer


def shorter_agent(session, question="Who handles enterprise escalations?"):
    """A cache was added, so a call disappeared."""
    session.model(
        "mock-llm-1",
        {"messages": [{"role": "user", "content": question}], "temperature": 0.0},
        fn=lambda: call_model([], 0.0),
    )
    return "cached"


def report(label, tape, agent):
    print("== {} ==".format(label))
    try:
        with agenttape.replay(tape) as session:
            agent(session)
        print("  no divergence: the run still matches the recording")
    except DivergenceError as exc:
        print("  {}".format(type(exc).__name__))
        for line in str(exc).splitlines():
            print("  | {}".format(line))
    print()


def main() -> None:
    workspace = Path(tempfile.mkdtemp(prefix="agenttape-example-"))
    tape = str(workspace / "golden.tape")

    try:
        with agenttape.record(tape) as session:
            golden_agent(session)

        print("golden run recorded: {} events".format(agenttape.Tape.describe(tape)["event_count"]))
        print()

        report("1. unchanged agent (control)", tape, golden_agent)
        report("2. a prompt edit", tape, changed_prompt)
        report("3. an 'innocent' reorder of independent calls", tape, reordered_calls)
        report("4. a caching change that removed a call", tape, shorter_agent)

        # ------------------------------------------------------------------ #
        # A tolerant mode exists for refactors that legitimately reorder calls.
        # It still records every divergence, and `verified` still goes false.
        # ------------------------------------------------------------------ #
        print("== 3b. the same reorder, with strict=False ==")
        with agenttape.replay(tape, strict=False, on_divergence="warn") as session:
            reordered_calls(session)
            print("  completed, but verified={}".format(session.verified))
            for record in session.divergences:
                print(
                    "  divergence at seq {}: expected {}, observed {}".format(
                        record["seq"],
                        (record["expected"] or {}).get("name"),
                        record["observed"]["name"],
                    )
                )
        print()

        # ------------------------------------------------------------------ #
        # The same technique as a regression test in CI.
        # ------------------------------------------------------------------ #
        print("== 5. replay as a CI gate ==")
        try:
            with agenttape.replay(tape) as session:
                changed_prompt(session)
            print("  PASS")
        except DivergenceError as exc:
            print("  FAIL: the agent's behaviour changed at seq {}".format(exc.seq))
            print("  (a prompt edit that fixes one scenario can silently break others;")
            print("   this is how you find out before your users do)")

    finally:
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    main()
