<div align="center">

# agenttape

**The 1Agent tape recorder.**

Record AI agent runs to local files. Replay them offline, deterministically.

[![CI](https://github.com/jomin2003/Agent-Tape/actions/workflows/ci.yml/badge.svg)](https://github.com/jomin2003/Agent-Tape/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%20%7C%203.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue.svg)](pyproject.toml)
[![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](pyproject.toml)
[![Tests](https://img.shields.io/badge/tests-354-brightgreen.svg)](tests)

Pure Python · zero runtime dependencies · library only

</div>

---

## The problem

An AI agent run is not reproducible.

Run the same program against the same task twice and you will usually get a
different trajectory: the model samples differently, the tools return different
data, the clock has moved on. This is not a bug in any one framework. It is
structural. An agent's control flow is produced by a stochastic sampler reading a
mutable outside world.

The consequences are concrete:

- **You cannot debug what you cannot reproduce.** A production failure you cannot
  re-execute is a story, not a defect.
- **You cannot regression-test a prompt.** A prompt edit that fixes one scenario
  and silently breaks five others is invisible, because there is no deterministic
  unit to test.
- **You cannot safely retry.** A retried agent that already issued a refund issues
  it twice.
- **You cannot audit.** Regulated decisions must be explainable after the fact,
  and "the model said something different that time" is not an explanation.

Setting `temperature=0` does not fix this. It cannot:

| Layer | Mechanism | Fixable by a client library? |
|---|---|---|
| Sampling | Temperature/top-p choose among near-tied tokens | No — must be **recorded** |
| Numerical | Kernel output depends on batch size; argmax flips on ULP-scale differences | No — must be **recorded** |
| Hardware | Mixed GPU fleets compute matmuls slightly differently | No — must be **recorded** |
| MoE routing | Unstable top-k routing, capacity-driven token dropping | No — must be **recorded** |
| External state | Tools return live data | No — must be **recorded** |
| Ambient state | Clock, RNG, env vars, UUIDs, filesystem | **Yes** — intercept and record |
| Scheduling | Concurrency, streaming boundaries, retries | Partly — record the interleaving |

Qwen3-235B-A22B, run 1000 times at `temperature=0`, produces **80 distinct
outputs**. OpenAI's `seed` parameter explicitly does not guarantee reproducibility;
Anthropic documents non-determinism at `temperature=0.0`. Across five LLMs
configured for deterministic output, accuracy varied by up to **15%** between
runs, with a **70%** spread between best and worst.

The single most important consequence, and the design premise of this library:
**a deterministic replay system is, first and foremost, a complete recording
system.** A library can only *control* ambient state and scheduling. Everything
else must be *captured*. Its correctness ceiling is the completeness of its
instrumentation.

→ Full literature review, taxonomy, and the ten open problems:
[`research/deterministic-replay-and-rollback-for-ai-agents.md`](research/deterministic-replay-and-rollback-for-ai-agents.md)

---

## What agenttape does

`agenttape` wraps every boundary an agent crosses and writes the answers to a
**tape** — an append-only, hash-chained, self-describing log on disk.

```python
import agenttape


def run_agent(session):
    hits = session.tool("search", ["refund policy"], fn=lambda: search("refund policy"))
    answer = session.model(
        "gpt-4o",
        {"messages": [{"role": "user", "content": hits}], "temperature": 0.0},
        fn=lambda: client.chat(hits),
    )
    session.outcome({"answer": answer})
    return answer


# Record: real calls, real money, real side effects.
with agenttape.record("runs/triage.tape") as session:
    run_agent(session)

# Replay: the agent's real logic, every answer served from the tape.
# No API calls. No tool execution. No side effects. No cost.
with agenttape.replay("runs/triage.tape") as session:
    run_agent(session)
```

The agent's own code is never recorded, bypassed, or rewritten. Only the answers
that came from *outside* it are. That is what makes replay a debugger: you change
your agent, replay the tape, and find out exactly where behaviour moved.

```python
from agenttape import verify, diff, check_determinism

print(verify("runs/triage.tape").render())
# tape runs/triage.tape  [OK]
#   events         6
#   chain          intact
#   manifest match True
#   ...

report = check_determinism("runs/triage.tape", run_agent, repeats=3)
assert report.deterministic
```

### What it does not do

Stated up front, because it matters:

- It **cannot make an arbitrary agent deterministic.** It can only replay what it
  recorded. Any unwrapped path — a bare `requests.get`, a subprocess, a C
  extension reading entropy — is a hole. The library fails loudly at the first
  call it cannot match, rather than papering over it, but it cannot see calls it
  was never wrapped around.
- It **cannot undo arbitrary side effects.** `rollback()` applies compensations
  you declared. A sent email has no inverse.
- It **does not do concurrency or streaming replay.** Single-process,
  synchronous, request/response agents only — the same boundary every current
  implementation draws.
- It **does not explain why the model said what it said.** Replay gives you the
  sequence of inputs and outputs, not interpretability.
- It **is not a sandbox.** "The tool is never invoked" is a property of this
  implementation, not a verifiable guarantee about your process.

---

## Installation

**Not on PyPI yet.** Install straight from the repository:

```bash
pip install "agenttape @ git+https://github.com/jomin2003/Agent-Tape@v0.1.1"
```

Or from a checkout, for development:

```bash
git clone https://github.com/jomin2003/Agent-Tape.git
cd Agent-Tape
pip install -e ".[dev]"
pytest
```

Requires Python 3.9+. No runtime dependencies — the standard library only, and
there is a test that enforces it.

Once the package is on PyPI this section becomes `pip install agenttape`; see
[RELEASING.md](RELEASING.md) for the checklist that gets it there.

---

## Usage

### Recording and replaying

```python
import agenttape

with agenttape.record("runs/session.tape", tags=["triage"], overwrite=True) as session:
    ...
```

`Session.record(path, *, seed=None, tags=None, meta=None, overwrite=False,
blob_threshold=65536, tape_id=None)`

`Session.replay(path, *, strict=True, on_divergence="raise", lookahead=256,
require_full_consumption=True, on_exhausted="raise", fork_to=None)`

### The boundaries you can record

| Boundary | Method |
|---|---|
| Model calls | `session.model(name, request, fn=...)` |
| Tool calls | `session.tool(name, args, kwargs=..., fn=...)` |
| Anything else | `session.exchange(kind, name, request, fn=...)` |
| Wall clock | `session.clock.time()`, `.now()`, `.utcnow()`, `.today()`, `.sleep()` |
| Randomness | `session.rng.random()`, `.choice()`, `.shuffle()`, `.randint()`, ... |
| Environment | `session.env(key)`, `session.read_text(path)`, `session.read_bytes(path)` |
| Identifiers | `session.uuid4()`, `session.token_hex(n)` |
| State checkpoints | `session.state(snapshot, label=...)` |
| Side effects | `session.effect(name, fn, compensate=...)` |
| Annotations | `session.mark(label)`, `session.log(message)` |
| Final result | `session.outcome(value)` |

Decorator form, for wrapping existing functions:

```python
with agenttape.record("runs/x.tape") as session:

    @session.tool_fn("search")
    def search(query, k=5):
        return index.query(query, k)
```

### Divergence: never guess

Matching is **sequence-primary with fingerprint validation**. The *n*-th call of a
replay must equal the *n*-th call of the recording, and the request fingerprint is
checked too, so the error tells you *what* changed.

```python
agenttape.replay("runs/x.tape")
# agenttape.errors.RequestMismatchError: replay diverged at seq 2: the agent's
# call does not match the recording
#   expected #2 model:gpt-4o {"messages":[{"content":"one"}],"temperature":0.0}
#   observed model:gpt-4o (same call, different arguments)
#     --- recorded
#     +++ replayed
#     @@ -1,4 +1,4 @@
#      {
#     -  "content": "one"
#     +  "content": "TWO"
#      }
```

Replay **never** falls through to a live system. If it cannot match a call, it
raises. A replay engine that guesses is worse than no replay engine, because it
manufactures false confidence.

Three failure modes, all subclasses of `DivergenceError`:

- `RequestMismatchError` — the call does not match the recording.
- `TapeExhaustedError` — the run is *longer* than the recording.
- `UnconsumedEventsError` — the run is *shorter* than the recording.

If you want a tape that replays loosely, `strict=False` searches the tape for a
matching request, and `on_divergence="warn"` records divergences instead of
raising. Both are recorded in `session.divergences`, and both make
`session.verified` false.

### Rollback

Effects are recorded with a compensation plan that lives **in the tape**, so a
rollback is itself a recorded, replayable action rather than an improvised second
unreproducible run.

```python
with agenttape.record("runs/booking.tape") as session:

    @session.compensator("refund")
    def refund(payload, result):
        return payments.refund(result["charge_id"])

    session.effect(
        "charge_card",
        charge,
        args=("acct_1", 100),
        compensate=("refund", {"reason": "booking aborted"}),
    )
    session.effect("reserve_seat", reserve, args=("seat_14C",))

    if not session.tool("confirm", ["seat_14C"], fn=confirm):
        session.rollback()  # newest first; already-compensated effects skipped
```

Replay the tape and `rollback()` reproduces the same sequence of compensations
**without calling the compensators** — replaying a tape that issued a refund does
not issue a refund. Register the compensator only if you intend to record.

### Counterfactual replay

Fork a recording at a point of interest, replay the prefix, then continue live
into a new tape. The original is never touched.

```python
with agenttape.replay(
    "runs/x.tape", on_exhausted="live", fork_to="runs/x-counterfactual.tape"
) as session:
    run_agent_with_a_different_prompt(session)
```

`diff("runs/x.tape", "runs/x-counterfactual.tape")` then tells you exactly where
the two runs parted.

### Verification

```python
agenttape.verify(path)  # chain, digest, blobs -> TapeReport
agenttape.diff(a, b)  # first difference between two tapes
agenttape.check_determinism(path, run)  # does this recording actually replay?
```

`check_determinism` is the self-test for a tape: it replays *n* times and confirms
every pass consumes the whole tape, reaches the same outcome, and produces the
same state fingerprints. A tape that fails it has a boundary the library is not
seeing — route that boundary through the session rather than loosening the check.

---

## Design

### The tape

```
runs/triage.tape/
├── manifest.json     # small; readable without touching the event log
├── events.jsonl      # one canonical-JSON event per line, append-only
└── blobs/<sha256>    # content-addressed payloads above the blob threshold
```

`manifest.json` answers "what is this tape?" by reading a few hundred bytes, which
matters when you are listing thousands of runs. `Tape.describe(path)` reads only
the manifest.

An event:

```json
{"seq":2,"kind":"model","name":"gpt-4o","ts":1758000000.5,
 "key":"9f2c...","request":{...},"response":{...},
 "status":"ok","error":null,"duration":0.42,"meta":{},
 "prev":"a4d2...","hash":"c81b..."}
```

Four decisions in that shape are load-bearing:

**Hash chain.** Each event's `hash` covers its body *including the previous
event's hash*. Editing or reordering any event invalidates every event after it.
`verify()` walks the chain and compares the head against the manifest digest.

**Hash the stored body, not the logical one.** Oversized payloads are replaced by
a blob reference carrying the SHA-256 of the bytes. Hashing the stored form makes
integrity verification a purely local operation over the event log, so a tape
whose blob store is damaged can still be checked for tampering — and the two
failure modes stay distinguishable. Blob contents are covered transitively by
content addressing.

**`fsync` before return.** `Tape.append` writes, flushes, and fsyncs *before* the
caller receives the value. The agent must never act on a value that is not yet on
disk. A crash then produces a *truncated* log (recoverable — the tail event is
discarded with a warning) rather than a *holed* one (an effect that happened with
no record of it, which is a lie).

**Failures are first-class events.** A tool that raised is recorded with
`status="error"` and a portable error record. On replay the same exception type is
re-raised, so the agent's `except` branches are reproduced faithfully. A tool that
timed out is usually the most interesting event in the run; it must not become a
gap.

### Matching

Replay holds a streaming cursor over the event log. The next call must match the
next recorded event on `(kind, name, fingerprint(request))`. Three properties
follow:

- **Any deviation is a real deviation**, not a tolerated reordering.
- **Errors can say what changed**, because the fingerprint is over the full
  request payload — the diff is generated from the recorded and observed requests.
- **Memory stays O(1)**, because the log is streamed, not loaded. That is what
  makes replay viable on multi-megabyte tapes.

`strict=False` additionally searches the tape (bounded by `lookahead`) and
considers events that were passed over earlier. It is useful for refactors that
legitimately reorder independent calls, and it is not the default, because
tolerance is exactly the property you do not want while debugging.

### Deterministic primitives

- **Clock.** Every reading is recorded, including the full datetime with its UTC
  offset, so replaying on a machine in another timezone yields what the original
  run saw. `sleep()` actually sleeps while recording and returns immediately while
  replaying — replaying an agent with backoff timers should not take minutes.
- **RNG.** `DeterministicRandom` seeds from the tape *and* records every draw.
  Seeding alone is enough if the draw sequence never changes; recording makes the
  guarantee robust to code changes, which is what you actually need. Only two
  primitives (`random()` and `getrandbits()`) are intercepted, because every
  other `random.Random` method is built on them.

### Canonical serialisation

Every equality check and every fingerprint goes through
`agenttape.canonical`. Two semantically identical values must produce
byte-identical output, on any machine, in any process. That means key-sorted
mappings, set elements sorted by their own encoding (Python's set order is not
stable across processes), symbolic NaN/infinity, and a hard error — not a
`repr()` fallback — for values with no deterministic encoding, since a default
`repr` contains a memory address.

There is a test that runs the encoder in subprocesses with different
`PYTHONHASHSEED` values and asserts the output is identical.

---

## Repository layout

```
agenttape/
├── src/agenttape/          # the library
│   ├── canonical.py        # deterministic serialisation and fingerprints
│   ├── events.py           # the event model and the hash chain
│   ├── tape.py             # the on-disk format
│   ├── channel.py          # the recording and replaying channels
│   ├── session.py          # the public facade
│   ├── clock.py            # recorded time
│   ├── entropy.py          # recorded randomness
│   ├── verify.py           # verify / diff / check_determinism
│   └── errors.py           # exception hierarchy
├── tests/                  # pytest suite
├── examples/               # runnable scripts, no network required
├── docs/                   # design notes, format spec, API reference
├── research/               # the literature review this library is built on
├── pyproject.toml
└── LICENSE                 # Apache-2.0
```

---

## Examples

Every example runs offline against a local mock, so you can run them all right
now:

```bash
PYTHONPATH=src python examples/01_minimal_record_and_replay.py
PYTHONPATH=src python examples/02_tool_using_agent.py
PYTHONPATH=src python examples/03_catching_divergence.py
PYTHONPATH=src python examples/04_determinism_check.py
PYTHONPATH=src python examples/05_rollback_and_fork.py
```

---

## Public API

Everything below is importable from the top-level package.

**Recording and replay**

| Name | Purpose |
|---|---|
| `Session` | The facade an agent program is handed |
| `record(path, **kw)` / `replay(path, **kw)` | Convenience constructors |
| `Recorder` / `Replayer` | The underlying channels |
| `UNSET` | Sentinel distinguishing "no response" from `None` |

**Storage**

| Name | Purpose |
|---|---|
| `Tape` | The on-disk format: create, open, append, stream, fork |
| `Event` | One recorded boundary crossing |
| `EventKind` | Kind constants (`MODEL`, `TOOL`, `CLOCK`, ...) |
| `GENESIS` | The `prev` value of the first event |
| `MANIFEST_NAME` / `EVENTS_NAME` | Layout constants |

**Deterministic primitives**

| Name | Purpose |
|---|---|
| `DeterministicClock` | Tape-backed time |
| `DeterministicRandom` | Tape-backed randomness |

**Verification**

| Name | Purpose |
|---|---|
| `verify(path)` | Integrity check → `TapeReport` |
| `diff(a, b)` | First difference → `TapeDiff` |
| `check_determinism(path, run)` | Replay self-test → `DeterminismReport` |
| `request_diff(a, b)` | Unified diff of two request payloads |

**Serialisation**

`canonical_dumps`, `canonical_loads`, `to_canonical`, `from_canonical`,
`fingerprint`, `sha256_hex`, `short_hash`, `TAG`, `ENCODER_ATTR`

**Errors**

`AgentTapeError` → `TapeError` (`TapeFormatError`, `TapeIntegrityError`),
`DivergenceError` (`RequestMismatchError`, `TapeExhaustedError`,
`UnconsumedEventsError`), `CanonicalizationError`, `SessionStateError`,
`CompensationError`, `RecordedError`.

Full reference: [`docs/api.md`](docs/api.md).

---

## Documentation

| Document | What it is for |
|---|---|
| [`docs/index.md`](docs/index.md) | The thirty-second version, and a map of the rest |
| [`docs/design.md`](docs/design.md) | Why the library is shaped this way — every design decision and the constraint that produced it |
| [`docs/determinism.md`](docs/determinism.md) | What this can and cannot make reproducible, including how to find the boundaries you have not wrapped |
| [`docs/tape-format.md`](docs/tape-format.md) | The on-disk format, specified for someone writing their own reader or writer |
| [`docs/api.md`](docs/api.md) | Reference for every public name |
| [`docs/faq.md`](docs/faq.md) | Straight answers, including "no" where the answer is no |
| [`research/`](research/deterministic-replay-and-rollback-for-ai-agents.md) | The literature review this library is built on, and the ten problems it does not solve |

---

## Testing

```bash
pytest                       # 354 tests, a few seconds, no network
pytest --cov=agenttape
```

The suite covers determinism across processes, hash-chain tamper detection,
truncated and corrupted logs, blob integrity, divergence in all three directions,
rollback ordering and idempotency, counterfactual forking, and the two project
constraints (no dependencies, no CLI).

---

## Status and scope

`0.1.1`. What works today, and what does not, is listed under
[What it does not do](#what-it-does-not-do). The short version: single-process,
synchronous agents; faithful replay; loud divergence; replayable rollback. Not
concurrency, not streaming, not multi-agent, not general-purpose undo.

The literature review in [`research/`](research/deterministic-replay-and-rollback-for-ai-agents.md)
names ten open problems in this area. `agenttape` solves none of them. It is a
deliberately narrow, well-tested attack on the part of the problem that is soluble
today, with the boundary documented rather than papered over.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The two rules that matter most: no
runtime dependencies, and replay never guesses.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
