"""Example 05 -- rollback and counterfactual replay.

Two of the harder questions in agent systems:

1. *If this run has to be abandoned, what do we undo, and how do we know the undo
   itself is correct?* Here the compensation plan is recorded in the tape, so a
   rollback is a recorded, replayable, auditable action -- not an improvised
   second run that nobody can reproduce.

2. *What if the agent had carried on instead of giving up?* Here the recording is
   replayed exactly, and once it runs out the run continues live into a forked
   tape. The original recording is never touched.

Run it with:

    PYTHONPATH=src python examples/05_rollback_and_fork.py
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import agenttape


class World:
    """A stand-in for the outside world, with a visible ledger of what happened."""

    def __init__(self):
        self.ledger: list[tuple] = []
        self.seats = {"14C", "14D", "15A"}

    def charge(self, account, amount):
        charge_id = "ch_{}".format(len(self.ledger) + 1)
        self.ledger.append(("charge", charge_id, account, amount))
        return {"charge_id": charge_id, "account": account, "amount": amount}

    def refund(self, payload, result):
        self.ledger.append(("refund", result["charge_id"], payload.get("reason")))
        return "refunded {}".format(result["charge_id"])

    def reserve(self, seat):
        if seat not in self.seats:
            raise ValueError("seat {} is taken".format(seat))
        self.seats.discard(seat)
        self.ledger.append(("reserve", seat))
        return {"seat": seat}

    def release(self, payload, result):
        self.seats.add(result["seat"])
        self.ledger.append(("release", result["seat"]))
        return "released {}".format(result["seat"])

    def confirm(self, seat):
        self.ledger.append(("confirm", seat))
        # 15A is never confirmable -- this is the failure the example is about.
        return seat in {"14C", "14D"}


def booking_agent(session, world, seat="15A", retry=None):
    """Book a seat; roll back on failure; optionally retry past the recording.

    With ``retry=None`` this agent's behaviour is fully described by the tape.
    With a ``retry`` seat, the retry steps are *new* work that happens after the
    recording has been consumed -- which is what makes it counterfactual.
    """
    session.mark("booking-start")

    session.effect(
        "charge_card",
        world.charge,
        args=("acct_1", 240),
        compensate=("refund", {"reason": "booking aborted"}),
    )
    session.effect("reserve_seat", world.reserve, args=(seat,), compensate="release")

    confirmed = session.tool("confirm_booking", [seat], fn=lambda: world.confirm(seat))

    if not confirmed:
        session.rollback()
        session.outcome({"status": "aborted", "seat": seat})

        if retry is None:
            return "aborted"

        # ------------------------------------------------------------------ #
        # Everything below happens *past the end of the recording*. During
        # replay these steps execute for real and are written to the fork.
        # ------------------------------------------------------------------ #
        session.mark("retry-after-abort")
        session.effect(
            "charge_card",
            world.charge,
            args=("acct_1", 240),
            compensate=("refund", {"reason": "booking aborted"}),
        )
        session.effect("reserve_seat", world.reserve, args=(retry,), compensate="release")
        ok = session.tool("confirm_booking", [retry], fn=lambda: world.confirm(retry))
        session.outcome({"status": "booked" if ok else "aborted", "seat": retry})
        return "booked" if ok else "aborted"

    session.outcome({"status": "booked", "seat": seat})
    return "booked"


def main() -> None:
    workspace = Path(tempfile.mkdtemp(prefix="agenttape-example-"))
    tape = str(workspace / "booking.tape")
    fork = str(workspace / "booking-counterfactual.tape")

    try:
        # ------------------------------------------------------------------ #
        # 1. Record a run that fails and rolls back.
        # ------------------------------------------------------------------ #
        world = World()
        print("== 1. recording a booking that fails and rolls back ==")
        with agenttape.record(tape) as session:
            session.compensator("refund", world.refund)
            session.compensator("release", world.release)
            outcome = booking_agent(session, world, seat="15A")

        print("  outcome: {}".format(outcome))
        print("  ledger:  {}".format(world.ledger))
        print("  seats:   {}".format(sorted(world.seats)))
        print()

        # ------------------------------------------------------------------ #
        # 2. Replay it. The compensators must NOT run again.
        # ------------------------------------------------------------------ #
        print("== 2. replaying -- no refunds issued, no seats released ==")
        quiet = World()
        with agenttape.replay(tape) as session:
            session.compensator("refund", quiet.refund)
            session.compensator("release", quiet.release)
            replayed = booking_agent(session, quiet, seat="15A")

        print("  outcome:  {}   (identical to the recording)".format(replayed))
        print("  ledger:   {}   <- empty: nothing actually happened".format(quiet.ledger))
        print("  seats:    {}   <- untouched".format(sorted(quiet.seats)))
        print("  verified: {}".format(session.verified))
        assert quiet.ledger == [], "replay must not re-issue refunds"
        assert replayed == outcome
        print()

        # ------------------------------------------------------------------ #
        # 3. Counterfactual: what if the agent had retried with another seat?
        # ------------------------------------------------------------------ #
        print("== 3. counterfactual: replay the recording, then continue live ==")
        counterfactual = World()

        with agenttape.replay(tape, on_exhausted="live", fork_to=fork) as session:
            session.compensator("refund", counterfactual.refund)
            session.compensator("release", counterfactual.release)
            result = booking_agent(session, counterfactual, seat="15A", retry="14C")
            print(
                "  forked at seq {} (the recording had {} events)".format(
                    session.fork_seq, session.fork_seq
                )
            )

        print("  result: {}".format(result))
        print("  ledger: {}".format(counterfactual.ledger))
        print("  note the first three entries are absent: the recorded prefix was")
        print("  replayed, not re-executed. Only the retry is real.")
        print()

        # ------------------------------------------------------------------ #
        print("== 4. the original recording is untouched ==")
        original_report = agenttape.verify(tape)
        fork_report = agenttape.verify(fork)
        print(
            "  original: {} events, digest {}".format(
                original_report.events, original_report.digest[:12]
            )
        )
        print(
            "  fork:     {} events, digest {}".format(fork_report.events, fork_report.digest[:12])
        )
        print("  fork parent: {}".format(agenttape.Tape.describe(fork)["parent"]["path"]))
        print()

        # ------------------------------------------------------------------ #
        print("== 5. where exactly did the two runs part? ==")
        print(agenttape.diff(tape, fork).render())
        print()

        # ------------------------------------------------------------------ #
        print("== 6. the rollback is on the tape, and the tape verifies ==")
        for event in agenttape.Tape.open(tape).iter_events():
            if event.kind in ("effect", "compensate", "outcome"):
                print("  {:>2}  {:<11} {}".format(event.seq, event.kind, event.name))
        print()
        print(agenttape.verify(tape).render())
        print()
        print("== 7. and the fork replays like any other recording ==")
        with agenttape.replay(fork) as session:
            replay_world = World()
            session.compensator("refund", replay_world.refund)
            session.compensator("release", replay_world.release)
            booking_agent(session, replay_world, seat="15A", retry="14C")
            print("  verified: {}   ledger: {}".format(session.verified, replay_world.ledger))

    finally:
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    main()
