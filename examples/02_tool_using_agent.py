"""Example 02 -- a realistic multi-step agent loop.

An agent that plans, calls tools in a loop, reads a file, and uses the clock and
the random number generator. Every one of those is a source of non-determinism,
and every one of them is recorded, so the replay is exact.

Run it with:

    PYTHONPATH=src python examples/02_tool_using_agent.py
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import agenttape

KNOWLEDGE = {
    "billing": ["Invoices are issued on the 1st.", "Refunds take 5 business days."],
    "security": ["2FA is required for all admin accounts."],
    "sla": ["P1 response time is 30 minutes."],
}


class FakeModel:
    """A tiny scripted 'model' that decides what to do next.

    Real agents get this decision from an LLM. What matters for the example is
    that the decision is made *inside* the agent, and only the model's answers
    are recorded -- so the agent's logic is fully re-run on replay.
    """

    def __init__(self):
        self.step = 0

    def __call__(self, messages, temperature):
        self.step += 1
        if self.step == 1:
            return {"action": "search", "query": "billing"}
        if self.step == 2:
            return {"action": "search", "query": "sla"}
        return {"action": "answer", "text": "P1 is 30 minutes; refunds take 5 business days."}


def search_index(query):
    return KNOWLEDGE.get(query, [])


def run_agent(session, model):
    """Plan -> act -> observe, with a bounded number of steps."""
    session.mark("start", goal="answer a support question")

    context = []
    for step in range(4):
        # The clock and RNG are recorded like anything else. An agent that
        # timestamps its transcript or jitters a retry would otherwise be
        # non-deterministic for reasons that have nothing to do with the model.
        started = session.clock.time()
        jitter = session.rng.uniform(0.0, 0.1)

        decision = session.model(
            "scripted-model",
            {"messages": context, "temperature": 0.0, "step": step},
            # The loop variable is bound explicitly. The callable is invoked
            # synchronously so capturing it by reference would work here, but
            # binding makes that independent of the caller's behaviour.
            fn=lambda messages=context: model(messages, 0.0),
        )

        if decision["action"] == "answer":
            session.state({"steps": step, "context": context}, label="before-answer")
            answer = decision["text"]
            session.outcome({"answer": answer, "steps": step})
            return answer

        query = decision["query"]
        hits = session.tool(
            "search",
            [query],
            kwargs={"k": 3},
            fn=lambda q=query: search_index(q),
        )
        context = [*context, {"role": "tool", "content": hits}]

        session.log(
            "step complete", step=step, jitter=jitter, elapsed=session.clock.time() - started
        )

    raise RuntimeError("agent did not converge")


def main() -> None:
    workspace = Path(tempfile.mkdtemp(prefix="agenttape-example-"))
    tape = workspace / "support.tape"

    try:
        print("== recording ==")
        with agenttape.record(tape, tags=["support"], meta={"task": "sla question"}) as session:
            answer = run_agent(session, FakeModel())
        print("answer: {!r}".format(answer))
        print("counts: {}".format(agenttape.Tape.describe(tape)["counts"]))
        print()

        print("== replaying (fresh model instance, so the agent really re-runs) ==")
        with agenttape.replay(tape) as session:
            replayed = run_agent(session, FakeModel())
        print("answer: {!r}".format(replayed))
        print("verified: {}".format(session.verified))
        assert replayed == answer
        print()

        print("== replay is much faster: no network, no waiting ==")
        import time

        started = time.perf_counter()
        for _ in range(20):
            with agenttape.replay(tape) as session:
                run_agent(session, FakeModel())
        elapsed = time.perf_counter() - started
        print("20 replays in {:.3f}s".format(elapsed))
        print()

        print("== the run, event by event ==")
        for event in agenttape.Tape.open(tape).iter_events():
            print(
                "  {:>2}  {:<8} {:<16} {}".format(
                    event.seq, event.kind, event.name, "ok" if event.ok else "ERROR"
                )
            )
        print()
        print(
            agenttape.check_determinism(
                tape,
                lambda session: run_agent(session, FakeModel()),
                repeats=3,
            ).render()
        )

    finally:
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    main()
