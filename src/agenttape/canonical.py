"""Canonical serialisation and fingerprinting.

Everything in :mod:`agenttape` that needs to be *compared* or *hashed* goes
through this module first. Two Python values that are semantically identical
must produce byte-identical canonical JSON, on any machine, in any process, in
any hash-seed configuration. Otherwise replay would report phantom divergences.

The rules:

* Mappings are key-sorted and rendered with no insignificant whitespace.
* Sets are sorted by their own canonical encoding (Python's iteration order for
  sets is not stable across processes, so it must never be trusted).
* ``float`` uses Python's shortest round-tripping ``repr``; NaN and infinities
  are encoded symbolically because JSON has no representation for them.
* Non-JSON-native types are tagged with a single reserved key,
  :data:`TAG`, so that the encoding is self-describing and reversible.
* Anything else raises :class:`~agenttape.errors.CanonicalizationError` in
  strict mode rather than falling back to ``repr()``, which for a default object
  contains its memory address and would make fingerprints non-reproducible.

Reversal is provided by :func:`from_canonical`, which makes the encoding a
genuine round-trip for every type the encoder understands.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import hashlib
import json
import math
import uuid as _uuid
from collections.abc import Mapping as _Mapping
from typing import Any, Callable, Optional

from .errors import CanonicalizationError

__all__ = [
    "TAG",
    "ENCODER_ATTR",
    "MAX_DEPTH",
    "to_canonical",
    "from_canonical",
    "canonical_dumps",
    "canonical_loads",
    "fingerprint",
    "sha256_hex",
    "short_hash",
    "is_blob_ref",
    "BlobResolver",
]

#: The single reserved key used to tag non-JSON-native values.
TAG = "__agenttape__"

#: Attribute an object may implement to supply its own canonical form.
ENCODER_ATTR = "__agenttape_encode__"

#: Guard against pathological or self-referential structures.
MAX_DEPTH = 64

#: A callable that maps a blob reference (SHA-256 hex) to its raw bytes.
BlobResolver = Callable[[str], bytes]


# --------------------------------------------------------------------------- #
# Encoding
# --------------------------------------------------------------------------- #

_SIMPLE = (bool, str, int)


def _key_to_str(key: Any, *, strict: bool) -> str:
    """Render a mapping key as a string without collisions.

    Plain strings pass through untouched (so ordinary dicts look ordinary).
    Anything else is prefixed with ``"\\x00"``, which cannot appear at the start
    of a legitimate string key in practice, and encoded canonically.
    """
    if isinstance(key, str):
        return key
    if isinstance(key, bool) or isinstance(key, int) or isinstance(key, float) or key is None:
        return "\x00" + canonical_dumps(key, strict=strict)
    return "\x00" + canonical_dumps(key, strict=strict)


def to_canonical(value: Any, *, strict: bool = True, _depth: int = 0) -> Any:
    """Convert *value* into a JSON-safe structure with a stable encoding.

    Args:
        value: Any Python value.
        strict: When ``True`` (the default), values with no deterministic
            encoding raise :class:`~agenttape.errors.CanonicalizationError`.
            When ``False``, they are encoded as an ``"opaque"`` tag carrying
            their ``repr`` -- which is *not* guaranteed to be reproducible and
            should only be used for human-facing summaries.

    Returns:
        A structure composed only of ``dict``, ``list``, ``str``, ``int``,
        ``float``, ``bool`` and ``None``.

    Raises:
        CanonicalizationError: In strict mode, for unencodable values.
    """
    if _depth > MAX_DEPTH:
        raise CanonicalizationError(
            "value nesting exceeded {} levels; the structure is probably "
            "self-referential".format(MAX_DEPTH)
        )

    # ``bool`` must be tested before ``int``: in Python, bool is a subclass of int.
    if value is None or isinstance(value, _SIMPLE):
        return value

    if isinstance(value, float):
        if math.isnan(value):
            return {TAG: "float", "value": "nan"}
        if math.isinf(value):
            return {TAG: "float", "value": "inf" if value > 0 else "-inf"}
        return value

    if isinstance(value, (bytes, bytearray, memoryview)):
        return {TAG: "bytes", "value": bytes(value).hex()}

    if isinstance(value, _dt.datetime):
        return {TAG: "datetime", "value": value.isoformat()}

    if isinstance(value, _dt.date):
        return {TAG: "date", "value": value.isoformat()}

    if isinstance(value, _dt.time):
        return {TAG: "time", "value": value.isoformat()}

    if isinstance(value, _dt.timedelta):
        return {TAG: "timedelta", "value": value.total_seconds()}

    if isinstance(value, _uuid.UUID):
        return {TAG: "uuid", "value": str(value)}

    if isinstance(value, _Mapping):
        return {
            _key_to_str(k, strict=strict): to_canonical(v, strict=strict, _depth=_depth + 1)
            for k, v in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [to_canonical(v, strict=strict, _depth=_depth + 1) for v in value]

    if isinstance(value, (set, frozenset)):
        # Sort by the canonical encoding so the result never depends on
        # Python's per-process string hashing.
        items = [to_canonical(v, strict=strict, _depth=_depth + 1) for v in value]
        items.sort(key=canonical_dumps)
        return {TAG: "set", "value": items}

    encoder = getattr(value, ENCODER_ATTR, None)
    if callable(encoder):
        return to_canonical(encoder(), strict=strict, _depth=_depth + 1)

    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            TAG: "dataclass",
            "type": "{}.{}".format(type(value).__module__, type(value).__qualname__),
            "value": {
                f.name: to_canonical(getattr(value, f.name), strict=strict, _depth=_depth + 1)
                for f in dataclasses.fields(value)
            },
        }

    if isinstance(value, BaseException):
        return {TAG: "exception", "value": _exception_record(value)}

    if strict:
        raise CanonicalizationError(
            "cannot canonically encode {}.{}: it has no deterministic "
            "representation. Implement {!r} on the type to return a JSON-safe "
            "form, pass plain data instead, or encode with strict=False if you "
            "only need a human-readable summary.".format(
                type(value).__module__, type(value).__qualname__, ENCODER_ATTR
            )
        )

    return {
        TAG: "opaque",
        "type": "{}.{}".format(type(value).__module__, type(value).__qualname__),
        "repr": repr(value),
    }


def _exception_record(exc: BaseException) -> dict:
    """Build the portable record stored for a raised exception."""
    return {
        "type": type(exc).__name__,
        "module": type(exc).__module__,
        "message": str(exc),
        "repr": repr(exc),
    }


# --------------------------------------------------------------------------- #
# Decoding
# --------------------------------------------------------------------------- #


def from_canonical(value: Any, *, blobs: Optional[BlobResolver] = None) -> Any:
    """Reverse :func:`to_canonical`.

    Args:
        value: A canonical structure produced by :func:`to_canonical` (or by
            ``json.loads`` of its textual form).
        blobs: Optional resolver used to materialise blob references. Without
            it, blob references are returned as their tagged ``dict``.

    Returns:
        The reconstructed Python value.
    """
    if isinstance(value, list):
        return [from_canonical(v, blobs=blobs) for v in value]

    if not isinstance(value, dict):
        return value

    tag = value.get(TAG)
    if not isinstance(tag, str):
        # An ordinary mapping that merely happens to be a dict.
        return {k: from_canonical(v, blobs=blobs) for k, v in value.items()}

    if tag == "bytes":
        return bytes.fromhex(value["value"])
    if tag == "datetime":
        return _dt.datetime.fromisoformat(value["value"])
    if tag == "date":
        return _dt.date.fromisoformat(value["value"])
    if tag == "time":
        return _dt.time.fromisoformat(value["value"])
    if tag == "timedelta":
        return _dt.timedelta(seconds=value["value"])
    if tag == "uuid":
        return _uuid.UUID(value["value"])
    if tag == "float":
        raw = value["value"]
        if raw == "nan":
            return float("nan")
        return float("inf") if raw == "inf" else float("-inf")
    if tag == "set":
        return {_freeze(v) for v in (from_canonical(i, blobs=blobs) for i in value["value"])}
    if tag == "dataclass":
        return {
            TAG: "dataclass",
            "type": value["type"],
            "value": {k: from_canonical(v, blobs=blobs) for k, v in value["value"].items()},
        }
    if tag == "exception":
        return dict(value["value"])
    if tag == "opaque":
        return value
    if tag == "blob":
        if blobs is None:
            return dict(value)
        payload = blobs(value["value"])
        return from_canonical(json.loads(payload.decode("utf-8")), blobs=blobs)

    # Unknown tag: leave it alone so forward-compatible readers degrade to raw data.
    return {k: from_canonical(v, blobs=blobs) for k, v in value.items()}


def _freeze(value: Any) -> Any:
    """Make a decoded value hashable so it can be put back into a ``set``."""
    if isinstance(value, dict):
        return tuple(sorted((k, _freeze(v)) for k, v in value.items()))
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


def is_blob_ref(value: Any) -> bool:
    """Return ``True`` if *value* is an unresolved blob reference."""
    return isinstance(value, dict) and value.get(TAG) == "blob"


# --------------------------------------------------------------------------- #
# Textual form and fingerprints
# --------------------------------------------------------------------------- #


def canonical_dumps(value: Any, *, strict: bool = True) -> str:
    """Serialise *value* to canonical JSON text.

    The output is deterministic: identical inputs always produce identical
    bytes, regardless of dict insertion order or ``PYTHONHASHSEED``.
    """
    return json.dumps(
        to_canonical(value, strict=strict),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_loads(text: str, *, blobs: Optional[BlobResolver] = None) -> Any:
    """Parse canonical JSON text back into Python values."""
    return from_canonical(json.loads(text), blobs=blobs)


def sha256_hex(data: bytes) -> str:
    """Return the lowercase hex SHA-256 digest of *data*."""
    return hashlib.sha256(data).hexdigest()


def fingerprint(*parts: Any, strict: bool = True) -> str:
    """Return a SHA-256 fingerprint over the canonical form of *parts*.

    Parts are separated by a unit separator so that ``("ab", "c")`` and
    ``("a", "bc")`` cannot collide.

    This is the function used to derive the *request key* of every tape event:
    the key is ``fingerprint(kind, name, request)``.
    """
    hasher = hashlib.sha256()
    for part in parts:
        hasher.update(canonical_dumps(part, strict=strict).encode("utf-8"))
        hasher.update(b"\x1f")
    return hasher.hexdigest()


def short_hash(value: str, length: int = 12) -> str:
    """Abbreviate a hex digest for display in logs and diffs."""
    if not value:
        return ""
    return value[:length]
