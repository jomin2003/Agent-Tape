"""The on-disk tape format: layout, durability, integrity, blobs, forking."""

from __future__ import annotations

import json
import os

import pytest

from agenttape import GENESIS, EventKind, Tape
from agenttape.errors import TapeError, TapeFormatError, TapeIntegrityError
from agenttape.tape import BLOBS_DIR, EVENTS_NAME, MANIFEST_NAME


def append_event(tape, name="search", response="hit"):
    from agenttape import fingerprint

    request = {"name": name}
    return tape.append(
        kind=EventKind.TOOL,
        name=name,
        key=fingerprint(EventKind.TOOL, name, request),
        request=request,
        response=response,
    )


# --------------------------------------------------------------------------- #
# Layout
# --------------------------------------------------------------------------- #


def test_create_lays_out_manifest_and_event_log(tmp_path):
    tape = Tape.create(tmp_path / "t.tape")
    assert (tmp_path / "t.tape" / MANIFEST_NAME).is_file()
    assert (tmp_path / "t.tape" / EVENTS_NAME).is_file()
    tape.close()


def test_manifest_is_human_readable_json(tmp_path):
    tape = Tape.create(tmp_path / "t.tape")
    tape.close()
    text = (tmp_path / "t.tape" / MANIFEST_NAME).read_text(encoding="utf-8")
    assert "\n" in text
    assert json.loads(text)["format"] == "agenttape"


def test_describe_reads_only_the_manifest(tmp_path):
    tape = Tape.create(tmp_path / "t.tape")
    append_event(tape)
    tape.close()
    # Remove the event log: describe() must still work.
    os.remove(tmp_path / "t.tape" / EVENTS_NAME)
    described = Tape.describe(tmp_path / "t.tape")
    assert described["format"] == "agenttape"
    assert described["closed"] is True


def test_describe_rejects_a_non_tape(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(TapeFormatError):
        Tape.describe(tmp_path / "empty")


def test_open_rejects_a_non_tape(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(TapeFormatError):
        Tape.open(tmp_path / "empty")


def test_open_rejects_a_future_format_version(tmp_path):
    tape = Tape.create(tmp_path / "t.tape")
    tape.close()
    manifest_path = tmp_path / "t.tape" / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["format_version"] = 999
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(TapeFormatError) as excinfo:
        Tape.open(tmp_path / "t.tape")
    assert "newer" in str(excinfo.value)


def test_open_rejects_a_foreign_format(tmp_path):
    target = tmp_path / "t.tape"
    target.mkdir()
    (target / MANIFEST_NAME).write_text(json.dumps({"format": "other"}), encoding="utf-8")
    with pytest.raises(TapeFormatError):
        Tape.open(target)


def test_create_refuses_to_clobber_without_overwrite(tmp_path):
    Tape.create(tmp_path / "t.tape").close()
    with pytest.raises(TapeError):
        Tape.create(tmp_path / "t.tape")


def test_overwrite_refuses_to_delete_a_directory_that_is_not_a_tape(tmp_path):
    target = tmp_path / "precious"
    target.mkdir()
    (target / "thesis.txt").write_text("years of work", encoding="utf-8")
    with pytest.raises(TapeError) as excinfo:
        Tape.create(target, overwrite=True)
    assert "refusing to overwrite" in str(excinfo.value)
    assert (target / "thesis.txt").is_file()


def test_overwrite_replaces_an_actual_tape(tmp_path):
    first = Tape.create(tmp_path / "t.tape")
    append_event(first, name="old")
    first.close()
    second = Tape.create(tmp_path / "t.tape", overwrite=True)
    assert second.event_count == 0
    second.close()


# --------------------------------------------------------------------------- #
# Appending and the chain
# --------------------------------------------------------------------------- #


def test_first_event_links_to_genesis(tmp_path):
    tape = Tape.create(tmp_path / "t.tape")
    event = append_event(tape)
    assert event.prev == GENESIS
    assert event.seq == 0
    tape.close()


def test_events_chain_to_their_predecessor(tmp_path):
    tape = Tape.create(tmp_path / "t.tape")
    first = append_event(tape, name="one")
    second = append_event(tape, name="two")
    assert second.prev == first.hash
    assert tape.digest == second.hash
    tape.close()


def test_digest_tracks_the_head(tmp_path):
    tape = Tape.create(tmp_path / "t.tape")
    assert tape.digest == GENESIS
    event = append_event(tape)
    assert tape.digest == event.hash
    tape.close()


def test_event_count_increments(tmp_path):
    tape = Tape.create(tmp_path / "t.tape")
    for index in range(5):
        append_event(tape, name="n{}".format(index))
    assert tape.event_count == 5
    tape.close()


def test_closed_tape_cannot_be_appended_to(tmp_path):
    tape = Tape.create(tmp_path / "t.tape")
    append_event(tape)
    tape.close()
    with pytest.raises(TapeError, match="closed"):
        Tape.open(tmp_path / "t.tape", writable=True)


def test_read_only_tape_cannot_be_appended_to(tmp_path):
    Tape.create(tmp_path / "t.tape").close()
    tape = Tape.open(tmp_path / "t.tape")
    with pytest.raises(TapeError):
        append_event(tape)


def test_close_writes_a_footer_and_seals_the_manifest(tmp_path):
    tape = Tape.create(tmp_path / "t.tape")
    append_event(tape)
    manifest = tape.close()
    assert manifest["closed"] is True
    assert manifest["digest"] == tape.digest
    assert manifest["counts"]["tool"] == 1
    kinds = [event.kind for event in Tape.open(tmp_path / "t.tape").iter_events()]
    assert kinds[-1] == EventKind.FOOTER


def test_close_is_idempotent(tmp_path):
    tape = Tape.create(tmp_path / "t.tape")
    first = tape.close()
    second = tape.close()
    assert first["digest"] == second["digest"]


def test_unclosed_tape_recovers_its_tail_on_reopen(tmp_path):
    tape = Tape.create(tmp_path / "t.tape")
    append_event(tape, name="one")
    append_event(tape, name="two")
    head = tape.digest
    tape.flush()
    # Simulate a crash: drop the handle without closing.
    tape._fh.close()
    tape._fh = None

    reopened = Tape.open(tmp_path / "t.tape", writable=True)
    assert reopened.digest == head
    assert reopened.event_count == 2
    third = append_event(reopened, name="three")
    assert third.seq == 2
    assert third.prev == head
    reopened.close()


def test_context_manager_closes_the_tape(tmp_path):
    with Tape.create(tmp_path / "t.tape") as tape:
        append_event(tape)
    assert Tape.describe(tmp_path / "t.tape")["closed"] is True


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #


def test_iter_events_streams_in_order(tmp_path):
    tape = Tape.create(tmp_path / "t.tape")
    for index in range(20):
        append_event(tape, name="n{}".format(index))
    tape.close()
    names = [event.name for event in Tape.open(tmp_path / "t.tape").iter_events()]
    assert names[:20] == ["n{}".format(i) for i in range(20)]


def test_events_round_trip_their_payloads(tmp_path):
    tape = Tape.create(tmp_path / "t.tape")
    payload = {"nested": {"list": [1, 2, {"deep": True}]}, "unicode": "café \u2713"}
    tape.append(
        kind=EventKind.TOOL,
        name="echo",
        key="k",
        request=payload,
        response=payload,
    )
    tape.close()
    event = next(Tape.open(tmp_path / "t.tape").iter_events())
    assert event.request == payload
    assert event.response == payload


def test_truncated_final_line_is_tolerated_with_a_warning(tmp_path):
    tape = Tape.create(tmp_path / "t.tape")
    append_event(tape, name="good")
    tape.close()
    events_path = tmp_path / "t.tape" / EVENTS_NAME
    with open(events_path, "a", encoding="utf-8") as handle:
        handle.write('{"seq": 99, "kind": "tool"')  # killed mid-write

    with pytest.warns(RuntimeWarning, match="truncated"):
        events = list(Tape.open(tmp_path / "t.tape").iter_events())
    assert any(event.name == "good" for event in events)


def test_corruption_in_the_middle_of_the_log_raises(tmp_path):
    tape = Tape.create(tmp_path / "t.tape")
    append_event(tape, name="one")
    append_event(tape, name="two")
    tape.close()
    events_path = tmp_path / "t.tape" / EVENTS_NAME
    lines = events_path.read_text(encoding="utf-8").splitlines()
    lines.insert(1, "{not json")
    events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(TapeIntegrityError):
        list(Tape.open(tmp_path / "t.tape").iter_events())


# --------------------------------------------------------------------------- #
# Blobs
# --------------------------------------------------------------------------- #


def test_large_payloads_become_blobs(tmp_path):
    tape = Tape.create(tmp_path / "t.tape", blob_threshold=128)
    big = "x" * 5000
    tape.append(kind=EventKind.TOOL, name="fetch", key="k", request={"q": 1}, response=big)
    tape.close()

    stored = next(Tape.open(tmp_path / "t.tape").iter_stored())
    assert stored["response"]["__agenttape__"] == "blob"
    assert (tmp_path / "t.tape" / BLOBS_DIR).is_dir()


def test_blobs_round_trip_through_reading(tmp_path):
    tape = Tape.create(tmp_path / "t.tape", blob_threshold=128)
    big = {"document": "y" * 5000}
    tape.append(kind=EventKind.TOOL, name="fetch", key="k", request={}, response=big)
    tape.close()
    event = next(Tape.open(tmp_path / "t.tape").iter_events())
    assert event.response == big


def test_small_payloads_stay_inline(tmp_path):
    tape = Tape.create(tmp_path / "t.tape", blob_threshold=1024)
    tape.append(kind=EventKind.TOOL, name="fetch", key="k", request={}, response="tiny")
    tape.close()
    stored = next(Tape.open(tmp_path / "t.tape").iter_stored())
    assert stored["response"] == "tiny"


def test_identical_payloads_share_one_blob(tmp_path):
    tape = Tape.create(tmp_path / "t.tape", blob_threshold=64)
    big = "z" * 4000
    for _ in range(5):
        tape.append(kind=EventKind.TOOL, name="fetch", key="k", request={}, response=big)
    tape.close()
    blobs = list((tmp_path / "t.tape" / BLOBS_DIR).iterdir())
    assert len(blobs) == 1


def test_put_blob_is_content_addressed(tmp_path):
    tape = Tape.create(tmp_path / "t.tape")
    ref = tape.put_blob(b"hello")
    assert tape.get_blob(ref) == b"hello"
    assert tape.has_blob(ref)
    assert tape.put_blob(b"hello") == ref
    tape.close()


def test_missing_blob_raises_integrity_error(tmp_path):
    tape = Tape.create(tmp_path / "t.tape", blob_threshold=64)
    tape.append(kind=EventKind.TOOL, name="f", key="k", request={}, response="q" * 500)
    tape.close()
    for blob in (tmp_path / "t.tape" / BLOBS_DIR).iterdir():
        blob.unlink()
    with pytest.raises(TapeIntegrityError):
        list(Tape.open(tmp_path / "t.tape").iter_events())


def test_tampered_blob_is_detected(tmp_path):
    tape = Tape.create(tmp_path / "t.tape", blob_threshold=64)
    tape.append(kind=EventKind.TOOL, name="f", key="k", request={}, response="q" * 500)
    tape.close()
    blob = next((tmp_path / "t.tape" / BLOBS_DIR).iterdir())
    blob.write_bytes(b"tampered")
    with pytest.raises(TapeIntegrityError) as excinfo:
        list(Tape.open(tmp_path / "t.tape").iter_events())
    assert "corrupted" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Forking
# --------------------------------------------------------------------------- #


def test_fork_copies_the_prefix(tmp_path):
    source = Tape.create(tmp_path / "src.tape")
    for index in range(5):
        append_event(source, name="n{}".format(index))
    source.close()

    fork = Tape.fork(tmp_path / "src.tape", tmp_path / "fork.tape", upto_seq=2)
    assert fork.event_count == 3
    assert [event.name for event in fork.iter_events()] == ["n0", "n1", "n2"]
    fork.close()


def test_fork_preserves_hashes_and_the_chain(tmp_path):
    source = Tape.create(tmp_path / "src.tape")
    events = [append_event(source, name="n{}".format(i)) for i in range(4)]
    source.close()

    fork = Tape.fork(tmp_path / "src.tape", tmp_path / "fork.tape")
    copied = list(fork.iter_events())
    assert [event.hash for event in copied] == [event.hash for event in events]
    assert [event.prev for event in copied] == [event.prev for event in events]
    fork.close()


def test_fork_records_its_lineage(tmp_path):
    source = Tape.create(tmp_path / "src.tape", seed=42)
    append_event(source)
    source.close()

    fork = Tape.fork(tmp_path / "src.tape", tmp_path / "fork.tape")
    parent = fork.manifest["parent"]
    assert parent["upto_seq"] == 0
    assert parent["digest"] == Tape.describe(tmp_path / "src.tape")["digest"]
    assert fork.manifest["seed"] == 42
    fork.close()


def test_fork_leaves_the_source_untouched(tmp_path):
    source = Tape.create(tmp_path / "src.tape")
    append_event(source)
    source.close()
    before = (tmp_path / "src.tape" / EVENTS_NAME).read_bytes()

    fork = Tape.fork(tmp_path / "src.tape", tmp_path / "fork.tape")
    append_event(fork, name="extra")
    fork.close()

    assert (tmp_path / "src.tape" / EVENTS_NAME).read_bytes() == before


def test_fork_copies_the_blob_store(tmp_path):
    source = Tape.create(tmp_path / "src.tape", blob_threshold=64)
    source.append(kind=EventKind.TOOL, name="f", key="k", request={}, response="w" * 900)
    source.close()

    fork = Tape.fork(tmp_path / "src.tape", tmp_path / "fork.tape")
    event = next(fork.iter_events())
    assert event.response == "w" * 900
    fork.close()


def test_fork_is_left_open_so_a_live_tail_can_be_appended(tmp_path):
    source = Tape.create(tmp_path / "src.tape")
    append_event(source)
    source.close()
    fork = Tape.fork(tmp_path / "src.tape", tmp_path / "fork.tape")
    assert not fork.closed
    assert fork.writable
    fork.close()
