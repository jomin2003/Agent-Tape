"""The tape: an append-only, hash-chained, self-describing on-disk log.

Layout
------

A tape is a directory::

    runs/support-triage.tape/
        manifest.json     # small; readable without touching the event log
        events.jsonl      # one canonical-JSON event per line, append-only
        blobs/<sha256>    # content-addressed payloads over the blob threshold

``manifest.json`` exists so that metadata questions ("what is this tape, how
many events, what is its digest, what did it fork from?") can be answered by
reading a few hundred bytes instead of a multi-megabyte log.

Durability
----------

Every :meth:`Tape.append` writes the line, flushes, and ``fsync``s **before
returning**. The ordering matters: the agent must not act on a value that is not
yet on disk, or a crash produces a *holed* log -- an effect that happened with
no record of it. A truncated log is recoverable (the tail event is discarded); a
holed log is a lie. This is the single most important invariant in the format.

Integrity
---------

Events form a hash chain: each event's ``hash`` covers its logical body
*including the previous event's hash*. Editing or reordering any event
invalidates every event after it. :func:`agenttape.verify` walks the chain and
compares the head against the manifest digest.

Blobs
-----

Payloads whose canonical form exceeds ``blob_threshold`` are written once to
``blobs/<sha256>`` and referenced by digest. This keeps tapes compact when the
same document or image flows through many steps, and keeps the event log
greppable. Blob references carry the content hash, so blob integrity is
checkable independently of the chain.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import time
import warnings
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, TextIO, Tuple, Union

from ._version import TAPE_FORMAT_VERSION, __version__
from .canonical import (
    TAG,
    canonical_dumps,
    fingerprint,
    from_canonical,
    sha256_hex,
)
from .errors import TapeError, TapeFormatError, TapeIntegrityError
from .events import GENESIS, Event, EventKind, event_hash

__all__ = ["Tape", "MANIFEST_NAME", "EVENTS_NAME", "BLOBS_DIR", "FORMAT_NAME"]

FORMAT_NAME = "agenttape"
MANIFEST_NAME = "manifest.json"
EVENTS_NAME = "events.jsonl"
BLOBS_DIR = "blobs"

PathLike = Union[str, os.PathLike]


class Tape:
    """A handle on a tape directory.

    Use :meth:`create` to write a new tape, :meth:`open` to read one, and
    :meth:`fork` to branch a recording. In day-to-day use you will not touch
    this class directly -- :class:`agenttape.Session` owns a ``Tape`` and
    exposes the ergonomic API -- but it is public and fully supported for
    tooling that needs to read tapes without replaying them.
    """

    #: Value of ``manifest["format"]`` for every tape this library writes.
    FORMAT = FORMAT_NAME

    #: Payloads larger than this (in canonical-JSON bytes) are stored as blobs.
    DEFAULT_BLOB_THRESHOLD = 64 * 1024

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #

    def __init__(self, path: PathLike, manifest: Dict[str, Any], *, writable: bool = False) -> None:
        self._path = Path(path)
        self._manifest = manifest
        self._writable = writable
        self._fh: Optional[TextIO] = None
        self._closed = bool(manifest.get("closed"))
        self._last_hash = manifest.get("digest") or GENESIS
        self._count = int(manifest.get("event_count") or 0)

    # -- create ------------------------------------------------------------- #

    @classmethod
    def create(
        cls,
        path: PathLike,
        *,
        tape_id: Optional[str] = None,
        seed: Optional[int] = None,
        tags: Optional[List[str]] = None,
        meta: Optional[Dict[str, Any]] = None,
        blob_threshold: int = DEFAULT_BLOB_THRESHOLD,
        overwrite: bool = False,
    ) -> "Tape":
        """Create a new, empty tape at *path*.

        Args:
            path: Directory to create. Created (with parents) if missing.
            tape_id: Stable identifier. Defaults to the directory name.
            seed: Seed recorded in the manifest. :class:`agenttape.Session`
                uses it to seed its deterministic RNG.
            tags: Free-form labels for filtering.
            meta: Arbitrary user metadata.
            blob_threshold: Canonical-JSON size above which a payload is stored
                as a content-addressed blob.
            overwrite: Replace an existing tape at *path*. Refuses to delete a
                non-empty directory that is not recognisably a tape.

        Raises:
            TapeError: If *path* exists and *overwrite* is ``False``, or if the
                existing directory is not a tape and cannot be safely replaced.
        """
        target = Path(path)
        if target.exists():
            if not overwrite:
                raise TapeError(
                    "a tape already exists at {}. Pass overwrite=True to replace it.".format(target)
                )
            cls._safe_remove(target)
        target.mkdir(parents=True, exist_ok=True)

        manifest = {
            "format": FORMAT_NAME,
            "format_version": TAPE_FORMAT_VERSION,
            "tape_id": tape_id or target.name,
            "created_at": _utc_now(),
            "closed": False,
            "closed_at": None,
            "seed": seed,
            "tags": list(tags or []),
            "meta": dict(meta or {}),
            "blob_threshold": int(blob_threshold),
            "event_count": 0,
            "digest": None,
            "genesis": GENESIS,
            "parent": None,
            "counts": {},
            "agenttape_version": __version__,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        }
        tape = cls(target, manifest, writable=True)
        tape._open_writer()
        tape.write_manifest()
        return tape

    # -- open --------------------------------------------------------------- #

    @classmethod
    def open(cls, path: PathLike, *, writable: bool = False) -> "Tape":
        """Open an existing tape for reading (or appending, if *writable*).

        Raises:
            TapeFormatError: If *path* is not a tape, or its format version is
                newer than this library understands.
        """
        target, manifest = cls._read_manifest(path)

        tape = cls(target, manifest, writable=writable)
        if writable:
            if tape._closed:
                raise TapeError(
                    "tape at {} is closed; it cannot be appended to. Fork it "
                    "instead: Tape.fork(path, new_path)".format(target)
                )
            tape._open_writer()
            # An unclosed tape may have events past whatever the manifest knows
            # about (a crashed run, or a fork awaiting its live tail).
            tail = _read_last_event(target / EVENTS_NAME)
            if tail is not None:
                tape._last_hash = tail.get("hash") or GENESIS
                tape._count = int(tail.get("seq", -1)) + 1
        return tape

    # -- describe (cheap metadata read) ------------------------------------- #

    @staticmethod
    def describe(path: PathLike) -> Dict[str, Any]:
        """Return the manifest without reading the event log.

        This is the O(1)-memory way to answer "what is this tape?" -- useful for
        listing thousands of runs.
        """
        _target, manifest = Tape._read_manifest(path)
        return manifest

    @staticmethod
    def _read_manifest(path: PathLike) -> Tuple[Path, Dict[str, Any]]:
        """Load and validate a tape's manifest, returning ``(path, manifest)``.

        Shared by :meth:`open` and :meth:`describe` so that both reject the same
        malformed files with the same errors. They used to differ: ``describe``
        returned whatever ``json.loads`` produced -- including a bare list -- and
        ``open`` then died on it with an ``AttributeError`` from ``.get``.
        """
        target = Path(path)
        manifest_path = target / MANIFEST_NAME
        if not manifest_path.is_file():
            raise TapeFormatError(
                "{} is not an agenttape: no {} found".format(target, MANIFEST_NAME)
            )
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise TapeFormatError("{} is not valid JSON: {}".format(manifest_path, exc)) from exc

        # Valid JSON is not enough: it has to be an object. A bare list or scalar
        # here means the wrong file, or a damaged one, and should say so.
        if not isinstance(manifest, dict):
            raise TapeFormatError(
                "{} does not contain a JSON object (found {})".format(
                    manifest_path, type(manifest).__name__
                )
            )

        if manifest.get("format") != FORMAT_NAME:
            raise TapeFormatError(
                "{} declares format {!r}, expected {!r}".format(
                    manifest_path, manifest.get("format"), FORMAT_NAME
                )
            )
        version = int(manifest.get("format_version") or 0)
        if version > TAPE_FORMAT_VERSION:
            raise TapeFormatError(
                "tape format version {} is newer than this library supports "
                "({}). Upgrade agenttape.".format(version, TAPE_FORMAT_VERSION)
            )
        return target, manifest

    # -- fork --------------------------------------------------------------- #

    @classmethod
    def fork(
        cls,
        source: PathLike,
        destination: PathLike,
        *,
        upto_seq: Optional[int] = None,
        overwrite: bool = False,
    ) -> "Tape":
        """Copy the first ``upto_seq + 1`` events of *source* into a new tape.

        Forking is how counterfactual replay works: branch a recording at a
        point of interest, replay the prefix, then continue live and record the
        alternative continuation. The new tape records its lineage in
        ``manifest["parent"]``.

        The prefix is copied verbatim -- event hashes and the ``prev`` chain are
        preserved, so the fork's prefix verifies against the original's. The
        fork is left *open* so a live tail can be appended.

        Args:
            source: Tape to fork from.
            destination: Directory for the new tape. Must not exist.
            upto_seq: Last sequence number to copy, inclusive. ``None`` copies
                everything up to the source's footer.
            overwrite: Replace an existing tape at *destination*.
        """
        src = cls.open(source)
        dest = cls.create(
            destination,
            tape_id=Path(destination).name,
            seed=src.manifest.get("seed"),
            tags=list(src.manifest.get("tags") or []),
            meta={
                "forked_from": str(src.path),
                "forked_at": _utc_now(),
                "fork_upto_seq": upto_seq,
            },
            blob_threshold=int(src.manifest.get("blob_threshold") or cls.DEFAULT_BLOB_THRESHOLD),
            overwrite=overwrite,
        )

        copied = 0
        last_hash = GENESIS
        for stored in src.iter_stored():
            if stored.get("kind") == EventKind.FOOTER:
                break
            if upto_seq is not None and int(stored.get("seq", -1)) > upto_seq:
                break
            dest._append_verbatim(stored)
            last_hash = stored.get("hash") or GENESIS
            copied += 1

        dest._last_hash = last_hash
        dest._count = copied
        dest.manifest["parent"] = {
            "path": str(src.path),
            "tape_id": src.manifest.get("tape_id"),
            "digest": src.manifest.get("digest"),
            "upto_seq": copied - 1 if copied else None,
        }

        # Copy the blob store so the fork is self-contained.
        src_blobs = src.path / BLOBS_DIR
        if src_blobs.is_dir():
            shutil.copytree(src_blobs, dest.path / BLOBS_DIR, dirs_exist_ok=True)

        dest.write_manifest()
        return dest

    # ------------------------------------------------------------------ #
    # Properties
    # ------------------------------------------------------------------ #

    @property
    def path(self) -> Path:
        """The tape directory."""
        return self._path

    @property
    def manifest(self) -> Dict[str, Any]:
        """The parsed manifest. Mutable; call :meth:`write_manifest` to persist."""
        return self._manifest

    @property
    def writable(self) -> bool:
        """``True`` if this handle can append events."""
        return self._writable

    @property
    def closed(self) -> bool:
        """``True`` if the tape has been finalised with a footer."""
        return self._closed

    @property
    def digest(self) -> str:
        """Hash of the most recent event -- the tape's fingerprint."""
        return self._last_hash

    @property
    def event_count(self) -> int:
        """Number of events written or known about."""
        return self._count

    @property
    def blob_threshold(self) -> int:
        """Canonical-JSON byte size above which payloads become blobs."""
        return int(self._manifest.get("blob_threshold") or self.DEFAULT_BLOB_THRESHOLD)

    # ------------------------------------------------------------------ #
    # Writing
    # ------------------------------------------------------------------ #

    def _open_writer(self) -> None:
        if self._fh is not None:
            return
        self._path.mkdir(parents=True, exist_ok=True)
        # The handle is owned by this Tape for its whole lifetime and is closed
        # in close(), so a context manager would be the wrong shape here.
        self._fh = open(  # noqa: SIM115
            self._path / EVENTS_NAME, "a", encoding="utf-8", newline="\n"
        )

    def _require_writable(self) -> None:
        if not self._writable:
            raise TapeError("tape at {} was opened read-only".format(self._path))
        if self._closed:
            raise TapeError(
                "tape at {} is closed; no further events may be appended".format(self._path)
            )
        self._open_writer()

    def append(
        self,
        *,
        kind: str,
        name: str,
        key: str,
        request: Any = None,
        response: Any = None,
        ts: Optional[float] = None,
        status: str = "ok",
        error: Optional[Dict[str, Any]] = None,
        duration: Optional[float] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Event:
        """Append one event and durably flush it.

        Returns the stored :class:`~agenttape.events.Event`, whose ``hash`` is
        the new head of the chain.
        """
        self._require_writable()
        event = Event(
            seq=self._count,
            kind=kind,
            name=name,
            key=key,
            request=request,
            response=response,
            ts=time.time() if ts is None else ts,
            status=status,
            error=error,
            duration=duration,
            meta=meta or {},
            prev=self._last_hash,
        )
        stored = self._encode_body(event.body())
        digest = event_hash(stored)
        stored["hash"] = digest
        self._write_stored(stored)
        event.hash = digest
        self._last_hash = digest
        self._count += 1
        return event

    def _append_verbatim(self, stored: Dict[str, Any]) -> None:
        """Write an already-encoded event, preserving its hash and chain link.

        Used by :meth:`fork`, which copies the prefix of another tape without
        re-encoding it -- so the fork's prefix is byte-identical to the source's.
        """
        self._require_writable()
        self._write_stored(stored)

    def _write_stored(self, stored: Dict[str, Any]) -> None:
        handle = self._fh
        if handle is None:  # pragma: no cover - defensive
            # Unreachable via append()/_append_verbatim(), both of which call
            # _require_writable() first. Kept explicit so the failure would be a
            # clear message rather than an AttributeError on None.
            raise TapeError("no open event log for {}".format(self._path))
        line = canonical_dumps(stored)
        handle.write(line + "\n")
        # Durability ordering: the event is on disk before the caller sees the
        # value. Without the fsync, a crash can produce a holed log.
        handle.flush()
        os.fsync(handle.fileno())

    def _encode_body(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Replace oversized top-level payloads with blob references.

        Metadata events (the run header and the closing footer) are never
        blobbed: they are small, they are what a human greps for when they open a
        tape, and pushing them into the blob store would make the event log
        unreadable for no benefit.
        """
        out = dict(body)
        if body.get("kind") in (EventKind.RECORD, EventKind.FOOTER):
            return out

        threshold = self.blob_threshold
        for field in ("request", "response"):
            payload = out.get(field)
            if payload is None:
                continue
            raw = canonical_dumps(payload).encode("utf-8")
            if len(raw) > threshold:
                ref = self.put_blob(raw)
                out[field] = {TAG: "blob", "value": ref, "size": len(raw)}
        return out

    def _decode_body(self, stored: Dict[str, Any]) -> Dict[str, Any]:
        return from_canonical(stored, blobs=self.get_blob)

    # ------------------------------------------------------------------ #
    # Reading
    # ------------------------------------------------------------------ #

    def iter_stored(self) -> Iterator[Dict[str, Any]]:
        """Stream raw stored bodies as they appear on disk, blobs unresolved.

        This is the byte-faithful view, used by integrity tooling. Each item is
        the parsed JSON object for one line, still including its ``hash`` field
        and still containing blob references.

        A malformed *final* line is tolerated with a warning -- that is the
        signature of a process killed mid-write, and the run is still usable up
        to that point. A malformed line anywhere else is corruption and raises
        :class:`~agenttape.errors.TapeIntegrityError`.
        """
        events_path = self._path / EVENTS_NAME
        if not events_path.is_file():
            return

        pending: Optional[tuple] = None  # (line number, raw text)
        with open(events_path, "r", encoding="utf-8") as handle:
            for lineno, raw in enumerate(handle, start=1):
                if not raw.strip():
                    continue
                if pending is not None:
                    raise TapeIntegrityError(
                        "corrupt event on line {} of {} (an earlier line was also "
                        "unparseable)".format(pending[0], events_path),
                        seq=pending[0] - 1,
                    )
                try:
                    yield json.loads(raw)
                except ValueError:
                    pending = (lineno, raw)

        if pending is not None:
            warnings.warn(
                "{} ends with a truncated event on line {}; the tape was probably "
                "written by a process that was killed. Reading everything before "
                "that point.".format(events_path, pending[0]),
                RuntimeWarning,
                stacklevel=2,
            )

    def iter_events(self) -> Iterator[Event]:
        """Stream events from disk, oldest first, with blobs resolved.

        Memory use is O(1): events are yielded as they are parsed. This is what
        makes replay viable on multi-megabyte tapes.

        Yields:
            :class:`~agenttape.events.Event` objects.
        """
        for stored in self.iter_stored():
            body = self._decode_body({k: v for k, v in stored.items() if k != "hash"})
            yield Event.from_body(body, hash=stored.get("hash", ""))

    def events(self) -> List[Event]:
        """Read the whole tape into a list.

        Convenient, and O(n) in memory. Prefer :meth:`iter_events` for anything
        that might be large.
        """
        return list(self.iter_events())

    def read(self) -> List[Event]:
        """Alias for :meth:`events`."""
        return self.events()

    def __iter__(self) -> Iterator[Event]:
        return self.iter_events()

    def __len__(self) -> int:
        return self._count

    # ------------------------------------------------------------------ #
    # Blobs
    # ------------------------------------------------------------------ #

    def blob_path(self, ref: str) -> Path:
        """Filesystem path of the blob with digest *ref*."""
        return self._path / BLOBS_DIR / ref

    def put_blob(self, data: bytes) -> str:
        """Store *data* content-addressed and return its SHA-256 digest.

        Writing the same bytes twice is a no-op, which is what makes repeated
        large payloads (a document replayed through ten steps) cost one copy.
        """
        ref = sha256_hex(data)
        target = self.blob_path(ref)
        if target.is_file():
            return ref
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
        return ref

    def get_blob(self, ref: str) -> bytes:
        """Read a blob, verifying that its content still matches its digest.

        Raises:
            TapeIntegrityError: If the blob is missing or its bytes were altered.
        """
        target = self.blob_path(ref)
        if not target.is_file():
            raise TapeIntegrityError("blob {} referenced by the tape is missing".format(ref))
        data = target.read_bytes()
        actual = sha256_hex(data)
        if actual != ref:
            raise TapeIntegrityError(
                "blob {} has digest {}; the blob store has been corrupted".format(ref, actual)
            )
        return data

    def has_blob(self, ref: str) -> bool:
        """``True`` if the blob store contains *ref*."""
        return self.blob_path(ref).is_file()

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def close(self, *, summary: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Finalise the tape: write the footer, update the manifest, close the file.

        Idempotent. On a read-only handle this is a no-op.

        Returns:
            The updated manifest.
        """
        if self._closed or not self._writable:
            return self._manifest

        summary = dict(summary or {})

        self.append(
            kind=EventKind.FOOTER,
            name="footer",
            key=fingerprint(EventKind.FOOTER, "footer", {}),
            request={},
            response=summary,
            meta={"closed_by": "agenttape {}".format(__version__)},
        )

        # Prefer counts supplied by the caller (a Session tracks them as it
        # records); otherwise derive them by streaming the log, which costs time
        # but no memory. The footer itself is excluded so that the counts mean
        # "how much agent work happened".
        counts = dict(summary.get("counts") or {}) or self._count_kinds()

        self._manifest["closed"] = True
        self._manifest["closed_at"] = _utc_now()
        self._manifest["event_count"] = self._count
        self._manifest["digest"] = self._last_hash
        self._manifest["counts"] = counts
        self.write_manifest()

        if self._fh is not None:
            self._fh.flush()
            os.fsync(self._fh.fileno())
            self._fh.close()
            self._fh = None
        self._closed = True
        return self._manifest

    def _count_kinds(self) -> Dict[str, int]:
        """Count events by kind, excluding the footer."""
        counts: Dict[str, int] = {}
        for event in self.iter_events():
            if event.kind == EventKind.FOOTER:
                continue
            counts[event.kind] = counts.get(event.kind, 0) + 1
        return counts

    def write_manifest(self) -> None:
        """Persist the in-memory manifest to disk atomically."""
        self._path.mkdir(parents=True, exist_ok=True)
        target = self._path / MANIFEST_NAME
        tmp = target.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(self._manifest, handle, indent=2, sort_keys=True, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)

    def flush(self) -> None:
        """Flush the event file to the OS. Rarely needed; :meth:`append` fsyncs."""
        if self._fh is not None:
            self._fh.flush()

    def __enter__(self) -> "Tape":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._writable and not self._closed:
            self.close(summary={"aborted": exc_type is not None})

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return "<Tape {} events={} closed={}>".format(self._path, self._count, self._closed)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _safe_remove(target: Path) -> None:
        """Remove *target* only if it is empty or recognisably an agenttape.

        A guard rail: ``overwrite=True`` must never be able to destroy anything
        that is not a tape. It used to unlink any file or symlink it found, so a
        path that pointed at a file rather than a directory -- a typo, or a
        misread argument -- was deleted without a word.
        """

        def refuse(reason: str) -> TapeError:
            return TapeError(
                "refusing to overwrite {}: {}. Remove it yourself if that is "
                "really what you want.".format(target, reason)
            )

        if target.is_symlink():
            raise refuse(
                "it is a symbolic link, and an overwrite should not decide whether to follow it"
            )
        if target.is_file():
            raise refuse("it is a file, not a tape directory")
        if not target.is_dir():
            return  # nothing there yet; create() will make the directory
        entries = list(target.iterdir())
        if not entries:
            target.rmdir()
            return
        manifest = target / MANIFEST_NAME
        looks_like_tape = False
        if manifest.is_file():
            try:
                looks_like_tape = (
                    json.loads(manifest.read_text(encoding="utf-8")).get("format") == FORMAT_NAME
                )
            except ValueError:
                looks_like_tape = False
        if not looks_like_tape:
            raise refuse("it is not an agenttape directory")
        shutil.rmtree(target)


# --------------------------------------------------------------------------- #
# Module helpers
# --------------------------------------------------------------------------- #


def _utc_now() -> str:
    """Current time as an ISO-8601 UTC string."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _read_last_event(events_path: Path) -> Optional[Dict[str, Any]]:
    """Return the last parseable JSON object in *events_path*, reading backwards.

    Only the tail of the file is read, so this stays cheap on a large tape.
    """
    if not events_path.is_file():
        return None
    chunk = 16 * 1024
    with open(events_path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        if position == 0:
            return None
        buffer = b""
        while position > 0:
            step = min(chunk, position)
            position -= step
            handle.seek(position)
            buffer = handle.read(step) + buffer
            lines = [line for line in buffer.split(b"\n") if line.strip()]
            if len(lines) >= 2 or position == 0:
                for line in reversed(lines):
                    try:
                        return json.loads(line.decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
                        continue
                return None
    return None
