"""Effects and rollback.

The design claim under test: a compensation plan lives *in the tape*, so a
rollback is a recorded, replayable action rather than an improvised one.
"""

from __future__ import annotations

import pytest

import agenttape as at
from agenttape.errors import CompensationError


@pytest.fixture
def ledger():
    """A stand-in for the outside world that records what actually happened."""
    return []


@pytest.fixture
def payments(ledger):
    """Charge/refund functions wired to the ledger."""

    def charge(account, amount):
        entry = {"id": "ch_{}".format(len(ledger) + 1), "account": account, "amount": amount}
        ledger.append(("charge", entry))
        return entry

    def refund(payload, result):
        ledger.append(("refund", result["id"], payload.get("reason")))
        return "refunded {}".format(result["id"])

    return charge, refund


@pytest.fixture
def workflow(payments):
    charge, refund = payments

    def run(session):
        session.effect(
            "charge_card",
            charge,
            args=("acct_1", 100),
            compensate=("refund", {"reason": "task aborted"}),
        )
        session.effect(
            "charge_card",
            charge,
            args=("acct_1", 50),
            compensate=("refund", {"reason": "task aborted"}),
        )
        return session.rollback()

    return run


# --------------------------------------------------------------------------- #
# Recording
# --------------------------------------------------------------------------- #


def test_effect_executes_and_records(tmp_path, payments, ledger):
    charge, _refund = payments
    path = str(tmp_path / "r.tape")
    with at.record(path) as session:
        result = session.effect("charge_card", charge, args=("acct_1", 100))
    assert result["amount"] == 100
    assert len(ledger) == 1
    event = next(e for e in at.Tape.open(path).iter_events() if e.kind == "effect")
    assert event.name == "charge_card"
    assert event.request["compensate"] is None


def test_effect_records_its_compensation_plan(tmp_path, payments):
    charge, _refund = payments
    path = str(tmp_path / "r.tape")
    with at.record(path) as session:
        session.effect(
            "charge_card", charge, args=("a", 1), compensate=("refund", {"reason": "x"})
        )
    event = next(e for e in at.Tape.open(path).iter_events() if e.kind == "effect")
    assert event.request["compensate"] == {"name": "refund", "payload": {"reason": "x"}}


def test_bare_string_compensation_gets_an_empty_payload(tmp_path, payments):
    charge, _refund = payments
    path = str(tmp_path / "r.tape")
    with at.record(path) as session:
        session.effect("charge_card", charge, args=("a", 1), compensate="refund")
    event = next(e for e in at.Tape.open(path).iter_events() if e.kind == "effect")
    assert event.request["compensate"] == {"name": "refund", "payload": {}}


def test_invalid_compensation_spec_is_rejected(tmp_path, payments):
    charge, _refund = payments
    with at.record(str(tmp_path / "r.tape")) as session:
        with pytest.raises(CompensationError):
            session.effect("charge_card", charge, args=("a", 1), compensate=("refund", "not-a-dict"))


def test_effects_are_listed_with_their_status(tmp_path, payments):
    charge, _refund = payments
    with at.record(str(tmp_path / "r.tape")) as session:
        session.effect("charge_card", charge, args=("a", 1), compensate="refund")
        session.effect("audit_log", response="logged")  # irreversible
    effects = session.effects
    assert effects[0]["compensator"] == "refund"
    assert effects[0]["compensated"] is False
    assert effects[1]["compensator"] is None


# --------------------------------------------------------------------------- #
# Rollback
# --------------------------------------------------------------------------- #


def test_rollback_compensates_in_reverse_order(tmp_path, payments, ledger):
    charge, refund = payments
    path = str(tmp_path / "r.tape")
    with at.record(path) as session:
        session.compensator("refund", refund)
        session.effect("charge_card", charge, args=("a", 1), compensate="refund")
        session.effect("charge_card", charge, args=("a", 2), compensate="refund")
        results = session.rollback()

    assert [r["ok"] for r in results] == [True, True]
    assert [r["effect_seq"] for r in results] == [2, 1]
    kinds = [entry[0] for entry in ledger]
    assert kinds == ["charge", "charge", "refund", "refund"]
    assert ledger[2][1] == "ch_2"  # newest compensated first


def test_rollback_records_compensations_on_the_tape(tmp_path, payments):
    charge, refund = payments
    path = str(tmp_path / "r.tape")
    with at.record(path) as session:
        session.compensator("refund", refund)
        session.effect("charge_card", charge, args=("a", 1), compensate="refund")
        session.rollback()
    counts = at.Tape.describe(path)["counts"]
    assert counts["compensate"] == 1


def test_rollback_is_idempotent(tmp_path, payments, ledger):
    charge, refund = payments
    path = str(tmp_path / "r.tape")
    with at.record(path) as session:
        session.compensator("refund", refund)
        session.effect("charge_card", charge, args=("a", 1), compensate="refund")
        session.rollback()
        second = session.rollback()
    assert second == []
    assert [entry[0] for entry in ledger] == ["charge", "refund"]


def test_rollback_dry_run_changes_nothing(tmp_path, payments, ledger):
    charge, refund = payments
    path = str(tmp_path / "r.tape")
    with at.record(path) as session:
        session.compensator("refund", refund)
        session.effect("charge_card", charge, args=("a", 1), compensate="refund")
        plan = session.rollback(dry_run=True)
    assert plan == [{"effect_seq": 1, "name": "charge_card", "compensator": "refund"}]
    assert [entry[0] for entry in ledger] == ["charge"]


def test_rollback_upto_leaves_earlier_effects_alone(tmp_path, payments, ledger):
    charge, refund = payments
    path = str(tmp_path / "r.tape")
    with at.record(path) as session:
        session.compensator("refund", refund)
        session.effect("charge_card", charge, args=("a", 1), compensate="refund")
        session.effect("charge_card", charge, args=("a", 2), compensate="refund")
        session.effect("charge_card", charge, args=("a", 3), compensate="refund")
        session.rollback(upto=2)
    assert [entry[0] for entry in ledger] == ["charge", "charge", "charge", "refund", "refund"]
    assert [entry[1] for entry in ledger[3:]] == ["ch_3", "ch_2"]


def test_missing_compensator_is_reported_not_raised(tmp_path, payments, ledger):
    charge, _refund = payments
    path = str(tmp_path / "r.tape")
    with at.record(path) as session:
        session.effect("charge_card", charge, args=("a", 1), compensate="never_registered")
        results = session.rollback()
    assert results[0]["ok"] is False
    assert "never_registered" in results[0]["error"]


def test_a_failing_compensator_does_not_stop_the_others(tmp_path, payments, ledger):
    charge, _refund = payments

    def flaky(payload, result):
        raise RuntimeError("refund gateway down")

    path = str(tmp_path / "r.tape")
    with at.record(path) as session:
        session.compensator("flaky", flaky)
        session.effect("charge_card", charge, args=("a", 1), compensate="flaky")
        session.effect("charge_card", charge, args=("a", 2), compensate="flaky")
        results = session.rollback()
    assert [r["ok"] for r in results] == [False, False]
    assert all("refund gateway down" in r["error"] for r in results)


def test_effects_without_a_plan_are_skipped_by_rollback(tmp_path, payments, ledger):
    charge, _refund = payments
    path = str(tmp_path / "r.tape")
    with at.record(path) as session:
        session.effect("send_email", response="sent")  # irreversible, no plan
        results = session.rollback()
    assert results == []


# --------------------------------------------------------------------------- #
# Replay
# --------------------------------------------------------------------------- #


def test_replay_reproduces_rollback_without_running_compensators(
    tmp_path, payments, ledger, workflow
):
    charge, refund = payments
    path = str(tmp_path / "r.tape")

    with at.record(path) as session:
        session.compensator("refund", refund)
        recorded = workflow(session)

    ledger.clear()

    with at.replay(path) as session:
        session.compensator("refund", refund)
        replayed = workflow(session)

    assert replayed == recorded
    assert ledger == [], "replay must not re-issue refunds"
    assert session.verified


def test_replay_does_not_need_the_compensator_to_be_registered(
    tmp_path, payments, ledger, workflow
):
    """Replay never calls the compensator, so registration is optional there."""
    charge, refund = payments
    path = str(tmp_path / "r.tape")
    with at.record(path) as session:
        session.compensator("refund", refund)
        workflow(session)

    ledger.clear()
    with at.replay(path) as session:
        results = workflow(session)  # no compensator registered this time
    assert all(r["ok"] for r in results)
    assert ledger == []


def test_replay_does_not_re_run_the_effect(tmp_path, payments, ledger, workflow):
    charge, refund = payments
    path = str(tmp_path / "r.tape")
    with at.record(path) as session:
        session.compensator("refund", refund)
        workflow(session)

    ledger.clear()
    with at.replay(path) as session:
        session.compensator("refund", refund)
        workflow(session)
    assert ledger == []


def test_compensator_can_be_used_as_a_decorator(tmp_path, payments, ledger):
    charge, _refund = payments
    path = str(tmp_path / "r.tape")
    with at.record(path) as session:

        @session.compensator("audit")
        def audit(payload, result):
            ledger.append(("audit", result))
            return "audited"

        session.effect("charge_card", charge, args=("a", 1), compensate="audit")
        session.rollback()
    assert ("audit", {"id": "ch_1", "account": "a", "amount": 1}) in ledger
