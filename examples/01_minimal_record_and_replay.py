"""Example 01 -- the smallest useful thing.

Record a two-step agent run, then replay it. The point of the example is what
does *not* happen on the second run: the model is never called and the tool is
never executed.

Run it with:

    PYTHONPATH=src python examples/01_minimal_record_and_replay.py
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import agenttape

# --------------------------------------------------------------------------- #
# A stand-in for the outside world. In a real agent these would be an HTTP call
# to a model provider and a call into a search index.
# --------------------------------------------------------------------------- #

CALLS: list[str] = []


def call_model(messages):
    """Pretend to call an LLM. Records that it was called, so we can prove it wasn't."""
    CALLS.append("model")
    question = messages[-1]["content"]
    if "refund" in question.lower():
        return "Refunds are available within 30 days of purchase."
    return "I don't have an answer for that."


def search(query):
    CALLS.append("search")
    return ["policy/refunds.md", "policy/returns.md"]


# --------------------------------------------------------------------------- #
# The agent. Note that it takes a session and knows nothing else about agenttape.
# --------------------------------------------------------------------------- #


def run_agent(session):
    hits = session.tool("search", ["refund policy"], fn=lambda: search("refund policy"))

    answer = session.model(
        "mock-llm-1",
        {
            "messages": [
                {"role": "system", "content": "Answer using the provided documents."},
                {"role": "user", "content": "How long do I have to return an item?"},
                {"role": "context", "content": hits},
            ],
            "temperature": 0.0,
        },
        fn=lambda: call_model([{"role": "user", "content": "How long?"}]),
    )

    session.outcome({"answer": answer})
    return answer


def main() -> None:
    workspace = Path(tempfile.mkdtemp(prefix="agenttape-example-"))
    tape = workspace / "minimal.tape"

    try:
        # ---------------------------------------------------------------- #
        # 1. Record. Real calls happen here.
        # ---------------------------------------------------------------- #
        print("== recording ==")
        with agenttape.record(tape) as session:
            answer = run_agent(session)

        print("answer:      {!r}".format(answer))
        print("live calls:  {}".format(CALLS))
        print("tape:        {}".format(tape))
        print("events:      {}".format(agenttape.Tape.describe(tape)["event_count"]))
        print()

        # ---------------------------------------------------------------- #
        # 2. Replay. Nothing live happens here.
        # ---------------------------------------------------------------- #
        CALLS.clear()
        print("== replaying ==")
        with agenttape.replay(tape) as session:
            replayed = run_agent(session)

        print("answer:      {!r}".format(replayed))
        print("live calls:  {}   <- the model and the tool were never invoked".format(CALLS))
        print("verified:    {}".format(session.verified))
        assert replayed == answer
        assert CALLS == [], "replay must not touch the outside world"
        print()

        # ---------------------------------------------------------------- #
        # 3. Inspect the tape without replaying it.
        # ---------------------------------------------------------------- #
        print("== the tape ==")
        print(agenttape.verify(tape).render())
        print()
        for event in agenttape.Tape.open(tape).iter_events():
            print("  {}".format(event.summary()))
        print()
        print("manifest:")
        for key in ("tape_id", "seed", "event_count", "digest", "counts"):
            print("  {:<12} {}".format(key, agenttape.Tape.describe(tape)[key]))

    finally:
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    main()
