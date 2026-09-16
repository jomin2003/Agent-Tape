# API reference

Everything documented here is importable from the top-level package.

```python
import agenttape
```

The public surface is enumerated in `agenttape.__all__`; there is a test that
every name in it exists and is documented.

---

## Recording and replay

### `Session`

The facade an agent program is handed. Every non-deterministic boundary is an
explicit method on it.

#### `Session.record(path, *, seed=None, tags=None, meta=None, overwrite=False, blob_threshold=65536, tape_id=None) -> Session`

Start recording to a new tape.

| Parameter | Default | Meaning |
|---|---|---|
| `path` | — | Directory for the tape. Created (with parents) if missing. |
| `seed` | `None` | Seed for the deterministic RNG. A fresh random seed is chosen if omitted, and recorded. |
| `tags` | `None` | Labels for filtering runs later. |
| `meta` | `None` | Arbitrary metadata stored in the manifest. |
| `overwrite` | `False` | Replace an existing tape. Refuses to delete a directory that is not recognisably a tape. |
| `blob_threshold` | `65536` | Canonical-JSON byte size above which a payload becomes a blob. |
| `tape_id` | `None` | Stable identifier; defaults to the directory name. |

Raises `TapeError` if the path exists and `overwrite` is false.

#### `Session.replay(path, *, strict=True, on_divergence="raise", lookahead=256, require_full_consumption=True, on_exhausted="raise", fork_to=None) -> Session`

Replay a recorded run.

| Parameter | Default | Meaning |
|---|---|---|
| `strict` | `True` | Match positionally. `False` also searches the tape for a matching request. |
| `on_divergence` | `"raise"` | `"raise"` or `"warn"`. Warn records divergences in `session.divergences` and tries to continue. |
| `lookahead` | `256` | Maximum events buffered while searching in non-strict mode. |
| `require_full_consumption` | `True` | Raise `UnconsumedEventsError` from `close()` if recorded events went unused. |
| `on_exhausted` | `"raise"` | `"raise"` or `"live"`. `"live"` continues executing for real once the recording runs out. |
| `fork_to` | `None` | Destination tape for a counterfactual run. Required when `on_exhausted="live"`. |

#### Methods

| Method | Returns | Purpose |
|---|---|---|
| `exchange(kind, name, request, *, fn=None, response=UNSET, meta=None)` | any | The primitive every other boundary is built on. |
| `model(name, request, *, fn=None, response=UNSET, meta=None)` | any | A model call. |
| `tool(name, args=None, *, kwargs=None, fn=None, response=UNSET, meta=None)` | any | A tool call. |
| `call(...)` | any | Alias for `exchange`. |
| `wrap(kind, name)` | decorator | Turn a function into a recorded boundary. |
| `model_fn(name)` / `tool_fn(name)` | decorator | `wrap` with a preset kind. |
| `env(key, default=None)` | `str \| None` | Read an environment variable. |
| `read_text(path, encoding="utf-8")` | `str` | Read a text file, recording its contents. |
| `read_bytes(path)` | `bytes` | Read a binary file, recording its contents. |
| `uuid4()` | `uuid.UUID` | A recorded UUID. |
| `token_hex(nbytes=16)` | `str` | A recorded random token. |
| `state(snapshot, *, label=None)` | `str` | Record a snapshot; returns its fingerprint. |
| `outcome(value)` | any | Record the run's final result. |
| `mark(label, **data)` | `None` | Annotate the tape. |
| `log(message, **data)` | `None` | A free-form note. |
| `effect(name, fn=None, *, args=(), kwargs=None, compensate=None, response=UNSET, meta=None)` | any | A side-effecting action with a compensation plan. |
| `compensator(name, fn=None)` | callable | Register a compensating action, by name. |
| `rollback(*, upto=None, dry_run=False)` | `list[dict]` | Apply compensations, newest first. |
| `close(*, check=None)` | `dict` | Finalise and return a summary. Idempotent. |

#### Properties

| Property | Type | Meaning |
|---|---|---|
| `mode` | `str` | `"record"` or `"replay"` (becomes `"record"` after a live tail). |
| `tape` | `Tape` | The underlying tape handle. |
| `path` | `Path` | Tape directory. |
| `seed` | `int \| None` | RNG seed. |
| `clock` | `DeterministicClock` | The tape-backed clock. |
| `rng` | `DeterministicRandom` | The tape-backed RNG. |
| `verified` | `bool` | Replay matched the recording exactly. Always `False` for a forked run. |
| `divergences` | `list[dict]` | Divergences observed (non-raising modes). |
| `consumed` / `remaining` | `int` | Events served / not served during replay. |
| `counts` | `dict` | Event counts by kind (recording sessions). |
| `outcome_value` / `outcome_fingerprint` | any / `str` | The recorded outcome. |
| `state_fingerprints` | `list[str]` | Fingerprints recorded by `state()`. |
| `effects` | `list[dict]` | Recorded effects and their compensation status. |
| `last_event` | `Event \| None` | The most recent event handled. |
| `closed` | `bool` | Whether `close()` has run. |
| `summary` | `dict` | The summary from `close()`. |
| `forked` / `fork_seq` | `bool` / `int \| None` | Whether a live tail began, and where. |

#### Module-level helpers

```python
agenttape.record(path, **kwargs) -> Session
agenttape.replay(path, **kwargs) -> Session
```

#### `UNSET`

Sentinel distinguishing "no response supplied" from `response=None`. It is falsy.

---

## Storage

### `Tape`

The on-disk format. Public and supported for tooling that reads tapes without
replaying them.

| Member | Purpose |
|---|---|
| `Tape.create(path, *, tape_id=None, seed=None, tags=None, meta=None, blob_threshold=65536, overwrite=False)` | Create a new tape. |
| `Tape.open(path, *, writable=False)` | Open an existing tape. |
| `Tape.describe(path)` | Return the manifest without reading the event log. |
| `Tape.fork(source, destination, *, upto_seq=None, overwrite=False)` | Copy a prefix into a new, open tape. |
| `append(*, kind, name, key, request=None, response=None, ts=None, status="ok", error=None, duration=None, meta=None)` | Append and durably flush one event. |
| `iter_events()` | Stream events with blobs resolved. O(1) memory. |
| `iter_stored()` | Stream raw stored bodies, blobs unresolved. |
| `events()` / `read()` | Read the whole tape into a list. O(n) memory. |
| `put_blob(data)` / `get_blob(ref)` / `has_blob(ref)` / `blob_path(ref)` | The content-addressed blob store. |
| `close(*, summary=None)` | Write the footer and seal the manifest. |
| `write_manifest()` / `flush()` | Persist metadata / flush the event file. |

Properties: `path`, `manifest`, `writable`, `closed`, `digest`, `event_count`,
`blob_threshold`. Supports `len(tape)` and iteration.

Constants: `Tape.FORMAT`, `Tape.DEFAULT_BLOB_THRESHOLD`, `MANIFEST_NAME`,
`EVENTS_NAME`.

### `Event`

One recorded boundary crossing.

Fields: `seq`, `kind`, `name`, `ts`, `key`, `request`, `response`, `status`,
`error`, `duration`, `meta`, `prev`, `hash`.

Methods: `body()`, `compute_hash()`, `from_body(body, *, hash="")`, `matches(kind,
name, key)`, `identity()`, `label()`, `summary(limit=80)`, `to_dict()`.
Property: `ok`.

### `EventKind`

String constants: `RECORD`, `MODEL`, `TOOL`, `CLOCK`, `RANDOM`, `ENV`, `EFFECT`,
`COMPENSATE`, `FORK`, `STATE`, `OUTCOME`, `MARK`, `LOG`, `FOOTER`, plus `ALL`.

### `GENESIS`

`"0" * 64` — the `prev` value of the first event.

### `event_hash(body)`

The one true event hash function. Takes a *stored* body and returns its SHA-256.

---

## Deterministic primitives

### `DeterministicClock`

Obtained from `session.clock`.

| Method | Returns |
|---|---|
| `time()` | Recorded `time.time()`. |
| `monotonic()` | Recorded `time.monotonic()`. |
| `now(tz=None)` | Recorded `datetime.now(tz)`, including the UTC offset. |
| `utcnow()` | Recorded `datetime.now(timezone.utc)`. |
| `today(tz=None)` | Recorded date. |
| `sleep(seconds)` | Sleeps while recording; returns immediately while replaying. |
| `isoformat(timespec="seconds")` | Recorded ISO-8601 string. |
| `__call__()` | Alias for `time()`. |

### `DeterministicRandom`

Obtained from `session.rng`. Subclasses `random.Random`, so every method
(`random`, `getrandbits`, `randint`, `choice`, `shuffle`, `sample`, `uniform`,
`gauss`, `randrange`, ...) is recorded. Adds `reseed(seed)`.

---

## Verification

### `verify(path, *, check_blobs=True, check_digest=True) -> TapeReport`

Walk the hash chain, compare the head against the manifest digest, and verify
every referenced blob. Never raises for a damaged tape — damage is reported in
`TapeReport.problems`. Raises `TapeFormatError` only if `path` is not a tape.

`TapeReport` fields: `path`, `ok`, `format_version`, `closed`, `events`, `counts`,
`digest`, `manifest_digest`, `digest_matches`, `chain_ok`, `first_bad_seq`,
`blobs_checked`, `blobs_ok`, `missing_blobs`, `parent`, `seed`, `created_at`,
`agenttape_version`, `problems`. Methods: `to_dict()`, `render()`.

### `diff(a, b, *, strict=False) -> TapeDiff`

Find the first point at which two tapes differ. By default only the call sequence
is compared; `strict=True` also compares recorded responses.

`TapeDiff` fields: `a`, `b`, `identical`, `common_events`,
`first_difference_seq`, `reason`, `expected`, `observed`, `detail`. Methods:
`to_dict()`, `render()`.

### `check_determinism(tape_path, run, *, repeats=2, strict=True, on_divergence="raise") -> DeterminismReport`

Replay *n* times and confirm every pass consumes the whole tape, reaches the same
outcome, and produces the same state fingerprints.

`DeterminismReport` fields: `tape`, `repeats`, `deterministic`, `runs`,
`problems`. Methods: `to_dict()`, `render()`.

### `request_diff(recorded, observed, *, context=1) -> str`

Unified diff between two request payloads, as used in divergence messages.

---

## Serialisation

| Function | Purpose |
|---|---|
| `to_canonical(value, *, strict=True)` | Convert to a JSON-safe structure with a stable encoding. |
| `from_canonical(value, *, blobs=None)` | Reverse it. |
| `canonical_dumps(value, *, strict=True)` | Canonical JSON text. |
| `canonical_loads(text, *, blobs=None)` | Parse canonical JSON text. |
| `fingerprint(*parts, strict=True)` | SHA-256 over the canonical form of the parts. |
| `sha256_hex(data)` | Lowercase hex SHA-256 of bytes. |
| `short_hash(value, length=12)` | Abbreviate a digest for display. |

Constants: `TAG` (`"__agenttape__"`, the reserved encoding key),
`ENCODER_ATTR` (`"__agenttape_encode__"`, the hook for custom encoding),
`MAX_DEPTH`.

---

## Errors

```
AgentTapeError
├── TapeError
│   ├── TapeFormatError        # not a tape, or an unsupported version
│   └── TapeIntegrityError     # chain, digest, or blob verification failed
├── CanonicalizationError      # a value has no deterministic encoding
├── DivergenceError            # every replay-time failure
│   ├── RequestMismatchError   # the call does not match the recording
│   ├── TapeExhaustedError     # the run is longer than the recording
│   └── UnconsumedEventsError  # the run is shorter than the recording
├── SessionStateError          # wrong mode, or used after close()
├── CompensationError          # a compensation could not be applied
└── RecordedError              # a recorded exception type was not importable
```

`DivergenceError` carries `seq`, `expected`, `observed`, `detail`, and a
`kind` class attribute. `as_dict()` returns a JSON-serialisable report.

`UnconsumedEventsError` additionally carries `remaining`.

`RecordedError` additionally carries `record`, the original error record.

---

## Type annotations

The package ships `py.typed` and is annotated throughout. `mypy src/agenttape`
should stay clean.
