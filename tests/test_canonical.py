"""Canonical serialisation and fingerprinting.

These are the foundation of every equality check in the library: if two
semantically identical values produce different canonical bytes, replay reports
phantom divergences. The tests therefore lean hard on order-independence and
cross-process stability.
"""

from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass

import pytest

import agenttape as at
from agenttape import (
    TAG,
    canonical_dumps,
    canonical_loads,
    fingerprint,
    from_canonical,
    to_canonical,
)
from agenttape.canonical import is_blob_ref
from agenttape.errors import CanonicalizationError

# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


def test_dict_key_order_does_not_matter():
    assert canonical_dumps({"a": 1, "b": 2}) == canonical_dumps({"b": 2, "a": 1})


def test_nested_key_order_does_not_matter():
    left = {"outer": {"x": [{"p": 1, "q": 2}], "y": 3}}
    right = {"outer": {"y": 3, "x": [{"q": 2, "p": 1}]}}
    assert canonical_dumps(left) == canonical_dumps(right)


def test_set_order_does_not_matter():
    # Set iteration order depends on PYTHONHASHSEED, so this is the classic trap.
    first = canonical_dumps({"tags": {"alpha", "beta", "gamma", "delta"}})
    second = canonical_dumps({"tags": {"delta", "gamma", "beta", "alpha"}})
    assert first == second


def test_frozenset_is_distinct_from_set():
    """They are equal in value but must not be conflated.

    Replay hands decoded responses back to the agent, so a recorded frozenset
    that came back as a set would break any code using it as a dict key or a set
    member. The tags therefore differ.
    """
    assert to_canonical(frozenset({"a"}))[TAG] == "frozenset"
    assert to_canonical({"a"})[TAG] == "set"
    assert canonical_dumps({"a"}) != canonical_dumps(frozenset({"a"}))


def test_set_and_frozenset_are_both_order_independent():
    assert canonical_dumps({"a", "b", "c"}) == canonical_dumps({"c", "b", "a"})
    assert canonical_dumps(frozenset({"a", "b", "c"})) == canonical_dumps(
        frozenset({"c", "b", "a"})
    )


def test_output_has_no_insignificant_whitespace():
    assert canonical_dumps({"a": [1, 2]}) == '{"a":[1,2]}'


def test_canonical_dumps_is_idempotent():
    value = {"b": [1, {"c": {2, 3}}], "a": None}
    once = canonical_dumps(value)
    twice = canonical_dumps(canonical_loads(once))
    assert once == twice


def test_stable_across_processes_with_different_hash_seeds(src_dir):
    """The encoding must not depend on PYTHONHASHSEED."""
    script = (
        "import agenttape as at;"
        "print(at.canonical_dumps({'b': 1, 'a': {'z', 'y', 'x'}, 'c': [{'q': 2, 'p': 1}]}))"
    )
    outputs = set()
    for seed in ("0", "1", "424242"):
        env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH=src_dir)
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, env=env, check=True
        )
        outputs.add(result.stdout.strip())
    assert len(outputs) == 1, "canonical form varied with PYTHONHASHSEED: {}".format(outputs)


# --------------------------------------------------------------------------- #
# Type support
# --------------------------------------------------------------------------- #


def test_tuple_encodes_as_list():
    assert to_canonical((1, 2)) == [1, 2]
    assert canonical_dumps((1, 2)) == canonical_dumps([1, 2])


def test_bytes_roundtrip():
    value = b"\x00\xff\x10hello"
    assert from_canonical(to_canonical(value)) == value


def test_bytearray_and_memoryview_encode_as_bytes():
    assert to_canonical(bytearray(b"ab")) == to_canonical(b"ab")
    assert to_canonical(memoryview(b"ab")) == to_canonical(b"ab")


def test_datetime_roundtrip_preserves_offset():
    value = dt.datetime(2026, 4, 12, 9, 30, tzinfo=dt.timezone.utc)
    assert from_canonical(to_canonical(value)) == value


def test_date_time_timedelta_roundtrip():
    assert from_canonical(to_canonical(dt.date(2026, 4, 12))) == dt.date(2026, 4, 12)
    assert from_canonical(to_canonical(dt.time(9, 30))) == dt.time(9, 30)
    assert from_canonical(to_canonical(dt.timedelta(seconds=90))) == dt.timedelta(seconds=90)


def test_uuid_roundtrip():
    value = uuid.UUID("12345678-1234-5678-1234-567812345678")
    assert from_canonical(to_canonical(value)) == value


def test_nan_and_infinity_are_encodable():
    encoded = to_canonical([float("nan"), float("inf"), float("-inf")])
    assert canonical_dumps(encoded)  # must not raise: allow_nan=False
    decoded = from_canonical(encoded)
    assert decoded[1] == float("inf") and decoded[2] == float("-inf")
    import math

    assert math.isnan(decoded[0])


def test_negative_zero_is_distinct_from_zero():
    assert canonical_dumps(-0.0) != canonical_dumps(0.0)


def test_non_string_keys_do_not_collide_with_string_keys():
    assert canonical_dumps({1: "a"}) != canonical_dumps({"1": "a"})


def test_exception_is_encoded_as_a_record():
    encoded = to_canonical(ValueError("boom"))
    assert encoded["__agenttape__"] == "exception"
    assert encoded["value"]["type"] == "ValueError"
    assert encoded["value"]["message"] == "boom"


def test_dataclass_is_encoded_by_field_name():
    @dataclass
    class Point:
        x: int
        y: int

    encoded = to_canonical(Point(1, 2))
    assert encoded["__agenttape__"] == "dataclass"
    assert encoded["value"] == {"x": 1, "y": 2}


def test_custom_encoder_attribute_is_used():
    class Money:
        def __init__(self, cents):
            self.cents = cents

        def __agenttape_encode__(self):
            return {"cents": self.cents}

    assert canonical_dumps(Money(250)) == canonical_dumps({"cents": 250})


# --------------------------------------------------------------------------- #
# Strictness
# --------------------------------------------------------------------------- #


def test_strict_mode_rejects_objects_without_a_stable_encoding():
    class Opaque:
        pass

    with pytest.raises(CanonicalizationError) as excinfo:
        canonical_dumps(Opaque())
    assert "__agenttape_encode__" in str(excinfo.value)


def test_non_strict_mode_falls_back_to_an_opaque_tag():
    class Opaque:
        pass

    encoded = canonical_dumps(Opaque(), strict=False)
    assert "opaque" in encoded


def test_nesting_limit_is_enforced():
    nested = current = {}
    for _ in range(at.canonical.MAX_DEPTH + 10):
        current["next"] = {}
        current = current["next"]
    with pytest.raises(CanonicalizationError):
        canonical_dumps(nested)


def test_blob_reference_is_left_alone_without_a_resolver():
    stored = {"__agenttape__": "blob", "value": "abc", "size": 3}
    assert from_canonical(stored) == stored


def test_blob_reference_is_resolved_with_a_resolver():
    payload = canonical_dumps({"big": True}).encode("utf-8")
    stored = {"__agenttape__": "blob", "value": "ref", "size": len(payload)}
    assert from_canonical(stored, blobs=lambda ref: payload) == {"big": True}


# --------------------------------------------------------------------------- #
# Fingerprints
# --------------------------------------------------------------------------- #


def test_fingerprint_is_a_sha256_hex_digest():
    value = fingerprint("a")
    assert len(value) == 64
    assert all(character in "0123456789abcdef" for character in value)


def test_fingerprint_parts_cannot_be_shifted_between_each_other():
    assert fingerprint("ab", "c") != fingerprint("a", "bc")


def test_fingerprint_is_stable_for_equal_values():
    assert fingerprint({"a": 1, "b": 2}) == fingerprint({"b": 2, "a": 1})


def test_fingerprint_detects_a_change_in_a_nested_value():
    assert fingerprint({"messages": [{"role": "user", "content": "one"}]}) != fingerprint(
        {"messages": [{"role": "user", "content": "two"}]}
    )


def test_short_hash_truncates():
    assert at.short_hash("abcdef123456", 6) == "abcdef"
    assert at.short_hash("") == ""


# --------------------------------------------------------------------------- #
# Reversal, tag by tag
# --------------------------------------------------------------------------- #
#
# The module docstring promises an exact inverse for some tags and a plain-data
# decoding for others. Both halves of that promise are pinned here, so the
# documentation cannot drift away from the behaviour.


@pytest.mark.parametrize(
    "value",
    [
        b"\x00\xff\x10hello",
        dt.datetime(2026, 4, 12, 9, 30, tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30))),
        dt.date(2026, 4, 12),
        dt.time(9, 30, 15),
        dt.timedelta(seconds=90, microseconds=5),
        uuid.UUID("12345678-1234-5678-1234-567812345678"),
        float("inf"),
        float("-inf"),
        {1, 2, 3},
        frozenset({"a", "b"}),
        "plain string",
        123,
        1.5,
        None,
        True,
        [1, {"nested": {2, 3}}],
    ],
    ids=lambda value: type(value).__name__,
)
def test_exactly_invertible_tags_round_trip(value):
    """These tags decode back to an equal value of the same type."""
    decoded = from_canonical(to_canonical(value))
    assert decoded == value
    assert type(decoded) is type(value)


def test_nan_round_trips_as_nan():
    decoded = from_canonical(to_canonical(float("nan")))
    import math

    assert math.isnan(decoded)


def test_dataclass_decodes_to_plain_data_not_an_instance():
    """Rebuilding the class would mean importing a name the tape supplies."""

    @dataclass
    class Point:
        x: int
        y: int

    decoded = from_canonical(to_canonical(Point(1, 2)))
    assert not isinstance(decoded, Point), "the tape must not be able to instantiate classes"
    assert decoded[TAG] == "dataclass"
    assert decoded["value"] == {"x": 1, "y": 2}
    assert decoded["type"].endswith("Point")


def test_exception_decodes_to_its_error_record():
    decoded = from_canonical(to_canonical(ValueError("boom")))
    assert decoded[TAG] == "exception"
    assert decoded["value"]["type"] == "ValueError"
    assert decoded["value"]["message"] == "boom"
    assert decoded["value"]["module"] == "builtins"


def test_opaque_decodes_to_the_tagged_dict():
    class Opaque:
        pass

    decoded = from_canonical(to_canonical(Opaque(), strict=False))
    assert decoded[TAG] == "opaque"


def test_an_unknown_tag_is_decoded_recursively_rather_than_failing():
    """A reader written against an older format must degrade, not raise."""
    payload = {TAG: "something-from-the-future", "value": {"nested": [1, 2]}}
    decoded = from_canonical(payload)
    assert decoded == payload


def test_sets_of_composite_values_round_trip():
    """Set elements must be frozen before they can go back into a set."""
    value = {(1, 2), (3, 4)}
    assert from_canonical(to_canonical(value)) == value


def test_set_of_dicts_round_trips_via_freezing():
    value = {"alpha", "beta", "gamma"}
    decoded = from_canonical(to_canonical(value))
    assert decoded == value


def test_a_frozen_dataclass_inside_a_set_round_trips():
    """Set members are frozen, so a tagged member decodes to a hashable value.

    This is the path that exercises the dict branch of the freezing helper: a
    frozen dataclass is hashable, so it can live in a set, and it decodes to a
    tagged mapping that has to become hashable again before it can go back in.
    """

    @dataclass(frozen=True)
    class Point:
        x: int
        y: int

    decoded = from_canonical(to_canonical({Point(1, 2)}))
    assert len(decoded) == 1
    member = next(iter(decoded))
    assert isinstance(member, tuple), "a tagged mapping must be frozen to stay hashable"
    assert ("value", (("x", 1), ("y", 2))) in member


def test_is_blob_ref_recognises_only_blob_tags():
    assert is_blob_ref({TAG: "blob", "value": "abc", "size": 3})
    assert not is_blob_ref({TAG: "bytes", "value": "6162"})
    assert not is_blob_ref({"value": "abc"})
    assert not is_blob_ref("not a dict")
    assert not is_blob_ref(None)


def test_blob_reference_resolution_is_recursive():
    """A blob nested inside a structure must still be resolved."""
    inner = canonical_dumps({"deep": {"payload": [1, 2, 3]}}).encode("utf-8")
    stored = {"outer": [{TAG: "blob", "value": "ref", "size": len(inner)}]}
    decoded = from_canonical(stored, blobs=lambda ref: inner)
    assert decoded == {"outer": [{"deep": {"payload": [1, 2, 3]}}]}
