# Tape format specification

**Format name:** `agenttape`
**Current format version:** `1`
**Status:** stable

The tape format is versioned independently of the library. Tapes are meant to
outlive the library that wrote them, so `format_version` is bumped only for
breaking changes to the layout below, and readers reject versions they do not
understand rather than guessing.

---

## 1. Layout

A tape is a directory:

```
<name>.tape/
├── manifest.json       # required; small metadata document
├── events.jsonl        # required; one event per line, append-only
└── blobs/              # optional; content-addressed payloads
    └── <sha256-hex>
```

The directory name is conventional and carries no meaning. `manifest.json` is the
only way to identify a tape.

---

## 2. `manifest.json`

UTF-8 JSON. Not part of the hash chain — it is a summary that can be regenerated.

```json
{
  "format": "agenttape",
  "format_version": 1,
  "tape_id": "triage.tape",
  "created_at": "2026-09-17T09:30:00.123456+00:00",
  "closed": true,
  "closed_at": "2026-09-17T09:30:02.456789+00:00",
  "seed": 17531345398507654011,
  "tags": ["triage"],
  "meta": {"task": "sla question"},
  "blob_threshold": 65536,
  "event_count": 6,
  "digest": "a4d20afede03aa572ba2f440ffbe3634d09e1890e2d9ec819787f0b3b0ed4312",
  "genesis": "0000000000000000000000000000000000000000000000000000000000000000",
  "parent": null,
  "counts": {"model": 1, "outcome": 1, "state": 1, "tool": 1},
  "agenttape_version": "0.1.0",
  "python": "3.13.14",
  "platform": "Windows-11-10.0.26100-SP0"
}
```

| Field | Type | Meaning |
|---|---|---|
| `format` | string | Always `"agenttape"`. |
| `format_version` | int | Layout version. A reader must refuse a version greater than it supports. |
| `tape_id` | string | Stable identifier; defaults to the directory name. |
| `created_at` | string | ISO-8601 UTC timestamp. |
| `closed` | bool | `true` once a footer has been written. |
| `closed_at` | string \| null | ISO-8601 UTC timestamp of the footer. |
| `seed` | int \| null | Seed for the session's deterministic RNG. |
| `tags` | array of string | Free-form labels. |
| `meta` | object | Arbitrary user metadata. |
| `blob_threshold` | int | Canonical-JSON byte size above which a payload becomes a blob. |
| `event_count` | int | Number of lines in `events.jsonl`, including header and footer. |
| `digest` | string \| null | `hash` of the last event. `null` while the tape is open. |
| `genesis` | string | 64 zeroes: the `prev` value of the first event. |
| `parent` | object \| null | Lineage, if the tape was forked. |
| `counts` | object | Event counts by kind, **excluding** the footer. |
| `agenttape_version` | string | Version of the library that wrote the tape. |
| `python` / `platform` | string | Environment, for forensics. |

`parent`, when present:

```json
{
  "path": "runs/original.tape",
  "tape_id": "original.tape",
  "digest": "2f6f434a5bc3...",
  "upto_seq": 7
}
```

An open tape (no footer yet) may have `event_count` and `digest` that lag the
event log, because a crashed process cannot update them. Readers that need exact
values must scan `events.jsonl`.

---

## 3. `events.jsonl`

UTF-8, newline-delimited. Each line is one canonical-JSON object with no
insignificant whitespace. Lines are appended and `fsync`ed individually.

### 3.1 Event object

```json
{
  "seq": 2,
  "kind": "model",
  "name": "gpt-4o",
  "ts": 1758000000.512345,
  "key": "9f2c1d4e...",
  "request": {"messages": [], "temperature": 0.0},
  "response": {"text": "..."},
  "status": "ok",
  "error": null,
  "duration": 0.42,
  "meta": {},
  "prev": "a4d20afede03...",
  "hash": "c81b7f9a0e12..."
}
```

| Field | Type | Meaning |
|---|---|---|
| `seq` | int | Zero-based position in the tape. |
| `kind` | string | Event kind (§3.2). |
| `name` | string | Stable name of the boundary, e.g. a model or tool name. |
| `ts` | float | Unix epoch seconds when the call started. |
| `key` | string | Request fingerprint: SHA-256 over the canonical form of `(kind, name, request)`. |
| `request` | any | Everything identifying the call. |
| `response` | any | The recorded answer, or `null` for a failed call. |
| `status` | string | `"ok"` or `"error"`. |
| `error` | object \| null | Portable error record (§3.3). |
| `duration` | float \| null | Wall-clock seconds the live call took. |
| `meta` | object | Free-form; **not** part of matching. |
| `prev` | string | `hash` of the previous event, or `genesis`. |
| `hash` | string | SHA-256 of the canonical form of the object **without** `hash`. |

### 3.2 Event kinds

| Kind | Written by | Meaning |
|---|---|---|
| `record` | `Session._write_header` | Sequence 0. Environment of the run. Tape metadata: skipped by replay. |
| `model` | `Session.model` | A model / LLM call. |
| `tool` | `Session.tool` | A tool call. |
| `clock` | `Session.clock` | A clock reading or sleep. |
| `random` | `Session.rng` | A draw from the RNG. |
| `env` | `Session.env`, `read_text`, `read_bytes`, `uuid4`, `token_hex` | An ambient-environment read. |
| `effect` | `Session.effect` | A side-effecting action, with its compensation plan. |
| `compensate` | `Session.rollback` | The application of a compensation. |
| `fork` | `Session._go_live` | Where a recording ended and a live tail began. Tape metadata: skipped by replay. |
| `state` | `Session.state` | A state snapshot and its fingerprint. |
| `outcome` | `Session.outcome` | The run's final result. |
| `mark` | `Session.mark` | A user annotation. |
| `log` | `Session.log` | A free-form note. |
| `footer` | `Tape.close` | Last event of a closed tape. Tape metadata: skipped by replay. |

Readers must tolerate unknown kinds: treat them as ordinary strings. Unknown
*fields* on an event are preserved under `meta["_unknown"]` rather than causing a
parse failure.

### 3.3 Error record

```json
{
  "type": "TimeoutError",
  "module": "builtins",
  "message": "upstream took too long",
  "repr": "TimeoutError('upstream took too long')"
}
```

A reader reconstructs the exception by resolving `module` + `type` from modules
that are **already imported**, then calling it with `message`. If that fails, it
must fall back to a generic error that preserves `type` and `message`, rather than
raising something unrelated. Nothing may be imported on a tape's behalf.

---

## 4. Canonical encoding

Every value on a tape is encoded by `agenttape.canonical`. The rules:

- Objects have their keys sorted; output has no insignificant whitespace.
- Arrays preserve order.
- Strings are UTF-8, unescaped except where JSON requires it (`ensure_ascii` is
  off).
- `float` uses Python's shortest round-tripping `repr`. `NaN` and infinities are
  encoded symbolically.
- Non-JSON-native values are tagged with the single reserved key
  `"__agenttape__"`:

| Tag | Encoding |
|---|---|
| `bytes` | `{"__agenttape__": "bytes", "value": "<hex>"}` |
| `datetime` / `date` / `time` | `{"__agenttape__": "datetime", "value": "<isoformat>"}` (and `date` / `time`) |
| `timedelta` | `{"__agenttape__": "timedelta", "value": <seconds>}` |
| `uuid` | `{"__agenttape__": "uuid", "value": "<canonical-uuid>"}` |
| `float` | `{"__agenttape__": "float", "value": "nan" \| "inf" \| "-inf"}` |
| `set` / `frozenset` | `{"__agenttape__": "set", "value": [<elements, sorted by their own canonical encoding>]}` |
| `dataclass` | `{"__agenttape__": "dataclass", "type": "<module>.<qualname>", "value": {...}}` |
| `exception` | `{"__agenttape__": "exception", "value": <error record>}` |
| `blob` | `{"__agenttape__": "blob", "value": "<sha256>", "size": <bytes>}` |
| `opaque` | `{"__agenttape__": "opaque", "type": "...", "repr": "..."}` — non-strict mode only |

Non-string object keys are prefixed with `"\x00"` and encoded canonically, so
`{1: "a"}` and `{"1": "a"}` cannot collide.

---

## 5. Hash chain

For each event, `hash` is:

```
sha256( canonical_json( event_object_without_hash ) )
```

`prev` is the previous event's `hash`, or `genesis` for sequence 0.

**The hash covers the stored form**, i.e. after oversized payloads have been
replaced by blob references. This makes verification a purely local operation
over `events.jsonl`: a tape whose blob store is damaged can still be checked for
tampering, and the two failure modes stay distinguishable.

The tape's `digest` is the `hash` of the last event.

### Verification procedure

1. Set `expected_prev = genesis`, `head = genesis`.
2. For each line, in order:
   a. Parse. A malformed **final** line is a truncated tail: warn and stop. A
      malformed line anywhere else is corruption: fail.
   b. Check `prev == expected_prev`. Otherwise the chain is broken at this line.
   c. Recompute `sha256(canonical_json(body_without_hash))` and compare with
      `hash`. A mismatch means this event was modified.
   d. `expected_prev = hash`; `head = hash`.
3. If the manifest is closed, `head` must equal `manifest.digest`.
4. For every blob reference encountered, read `blobs/<value>` and confirm its
   SHA-256 equals `<value>`.

`agenttape.verify(path)` performs all of this and returns a `TapeReport`.

---

## 6. Blobs

A payload whose canonical-JSON encoding exceeds `blob_threshold` bytes is written
once to `blobs/<sha256-of-bytes>` and replaced by a blob reference. Writing the
same bytes twice is a no-op, so a document replayed through ten steps costs one
copy.

Blobs are written to a temporary file, `fsync`ed, then `os.replace`d into place, so
a blob is either fully present or absent — never half-written.

Metadata events (`record` and `footer`) are never blobbed. They are small, and
they are what a human greps for when they open a tape.

Blob integrity is verified by content addressing: the filename *is* the digest of
the contents.

---

## 7. Forking

A fork is a new tape whose first *n* events are byte-identical copies of another
tape's first *n* events, including their `hash` and `prev` values. Because the
chain is preserved, the fork's prefix verifies against the original.

A fork is written **open** (no footer), so a live tail can be appended. The
lineage is recorded in `manifest.parent`, and the branch point is marked in the
event stream by a `fork` event.

Forking is what makes counterfactual replay possible: replay the prefix, then
continue executing for real, writing the alternative continuation into the fork
while the original stays untouched.

---

## 8. Version history

| Version | Date | Change |
|---|---|---|
| 1 | 2026-09-17 | Initial format. |

---

## 9. Compatibility rules for implementers

If you are writing a reader:

- Reject `format_version` greater than what you support. Do not guess.
- Tolerate unknown event kinds and unknown event fields.
- Treat a malformed final line as a truncated tail, not as corruption.
- Never import a module on a tape's behalf when reconstructing an exception.
- Do not assume `manifest.event_count` is exact for an open tape.

If you are writing a writer:

- Never rewrite an existing event line. Append only.
- `fsync` before the caller can act on the value.
- Bump `format_version` for any breaking change to this document.
