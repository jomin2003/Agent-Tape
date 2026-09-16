"""The event model and the hash chain."""

from __future__ import annotations

import json

from agenttape import GENESIS, Event, EventKind, fingerprint, rebuild_error
from agenttape.errors import RecordedError


def make_event(**overrides):
    data = {
        "seq": 0,
        "kind": EventKind.TOOL,
        "name": "search",
        "key": fingerprint(EventKind.TOOL, "search", {"q": "x"}),
        "request": {"q": "x"},
        "response": ["a"],
        "ts": 1000.0,
    }
    data.update(overrides)
    event = Event(**data)
    event.hash = event.compute_hash()
    return event


def test_genesis_is_sixty_four_zeroes():
    assert GENESIS == "0" * 64


def test_body_excludes_the_hash():
    event = make_event()
    assert "hash" not in event.body()
    assert "prev" in event.body()


def test_hash_is_deterministic_for_the_same_body():
    assert make_event().compute_hash() == make_event().compute_hash()


def test_hash_changes_when_the_response_changes():
    assert make_event().compute_hash() != make_event(response=["b"]).compute_hash()


def test_hash_changes_when_the_previous_link_changes():
    assert make_event().compute_hash() != make_event(prev="a" * 64).compute_hash()


def test_hash_changes_when_the_sequence_changes():
    assert make_event().compute_hash() != make_event(seq=1).compute_hash()


def test_default_prev_is_genesis():
    assert make_event().prev == GENESIS


def test_from_body_round_trips():
    original = make_event()
    restored = Event.from_body(original.body(), hash=original.hash)
    assert restored.body() == original.body()
    assert restored.hash == original.hash


def test_from_body_preserves_unknown_fields_under_meta():
    body = make_event().body()
    body["future_field"] = {"added": "later"}
    restored = Event.from_body(body)
    assert restored.meta["_unknown"] == {"future_field": {"added": "later"}}


def test_matches_requires_kind_name_and_key():
    event = make_event()
    assert event.matches(EventKind.TOOL, "search", event.key)
    assert not event.matches(EventKind.MODEL, "search", event.key)
    assert not event.matches(EventKind.TOOL, "other", event.key)
    assert not event.matches(EventKind.TOOL, "search", "different")


def test_identity_is_the_matching_triple():
    event = make_event()
    assert event.identity() == {
        "kind": EventKind.TOOL,
        "name": "search",
        "key": event.key,
    }


def test_ok_reflects_status():
    assert make_event().ok
    assert not make_event(status="error").ok


def test_label_and_summary_are_readable():
    event = make_event()
    assert event.label() == "#0 tool:search"
    assert "search" in event.summary()
    assert (
        "[ValueError]"
        in make_event(status="error", error={"type": "ValueError", "message": "bad"}).summary()
    )


def test_to_dict_includes_the_hash():
    event = make_event()
    assert event.to_dict()["hash"] == event.hash


# --------------------------------------------------------------------------- #
# Error reconstruction
# --------------------------------------------------------------------------- #


def test_rebuild_error_restores_a_builtin_exception_type():
    rebuilt = rebuild_error({"type": "ValueError", "module": "builtins", "message": "boom"})
    assert isinstance(rebuilt, ValueError)
    assert str(rebuilt) == "boom"


def test_rebuild_error_restores_a_third_party_exception_type():
    rebuilt = rebuild_error({"type": "KeyError", "module": "builtins", "message": "'missing'"})
    assert isinstance(rebuilt, KeyError)


def test_rebuild_error_falls_back_when_the_type_is_gone():
    rebuilt = rebuild_error(
        {"type": "GhostError", "module": "nowhere.at.all", "message": "vanished"}
    )
    assert isinstance(rebuilt, RecordedError)
    assert "GhostError" in str(rebuilt)


def test_rebuild_error_attaches_the_original_record():
    rebuilt = rebuild_error({"type": "ValueError", "module": "builtins", "message": "boom"})
    assert rebuilt.agenttape_record["type"] == "ValueError"


def test_rebuild_error_tolerates_a_non_dict_record():
    assert isinstance(rebuild_error("just a string"), RecordedError)


def test_recorded_error_is_an_agenttape_error():
    from agenttape import AgentTapeError

    assert isinstance(RecordedError({}), AgentTapeError)


# --------------------------------------------------------------------------- #
# Kinds
# --------------------------------------------------------------------------- #


def test_all_kinds_are_listed():
    assert EventKind.MODEL in EventKind.ALL
    assert EventKind.FOOTER in EventKind.ALL
    assert len(EventKind.ALL) == len(set(EventKind.ALL))


def test_kinds_are_plain_strings_so_tapes_stay_forward_compatible():
    assert isinstance(EventKind.MODEL, str)
    assert json.dumps({"kind": EventKind.MODEL}) == '{"kind": "model"}'
