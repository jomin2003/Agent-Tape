# Determinism: what this library can and cannot make reproducible

This page is the honest one. It is written for someone deciding whether
`agenttape` will solve their problem.

---

## The short version

`agenttape` makes an agent run reproducible **to the extent that the run's
non-determinism passes through the session**. It does that faithfully and
loudly. It cannot make an arbitrary agent deterministic, and it cannot see a
boundary it was not wrapped around.

If you take one thing from this page: **the failure mode of an incomplete
recording is not a wrong replay, it is a replay that stops with a diff.** That is
the best available outcome, and it is still a real limitation.

---

## The seven layers

| Layer | Mechanism | Can `agenttape` help? |
|---|---|---|
| **1. Sampling** | Temperature/top-p/top-k pick among near-tied tokens | No. Must be **recorded**. |
| **2. Numerics** | Kernel output depends on batch size; argmax flips on ULP-scale differences | No. Must be **recorded**. |
| **3. Hardware** | Mixed GPU fleets compute matmuls slightly differently | No. Must be **recorded**. |
| **4. MoE routing** | Unstable top-k routing; capacity-driven token dropping | No. Must be **recorded**. |
| **5. External state** | Tools return live data | No. Must be **recorded**. |
| **6. Ambient state** | Clock, RNG, env vars, UUIDs, filesystem, hash ordering | **Yes.** Intercepted and recorded. |
| **7. Scheduling** | Concurrency, streaming boundaries, retry timing | Partly. Recorded, not controlled. |

Layers 1–5 are the reason `temperature=0` is not enough, and they are the reason
the library is fundamentally a *recording* system. Layers 6 and 7 are what it can
actually fix, and layer 6 is where nearly all the accidental non-determinism in a
well-written agent lives.

---

## What "deterministic" means here

Given an agent program `P`, a task `T`, and a recorded run `R`, replaying `R`
re-executes `P` on `T` and produces **the same observable trajectory**: the same
sequence of boundary crossings, with the same answers, in the same order.

It does *not* mean:

- the same internal state,
- the same hidden model reasoning,
- the same wall-clock duration,
- the same behaviour if you change `P`.

The last one is a feature. Changing `P` and re-running the tape is the primary
use case, and the point of the exercise is that the trajectory *does* change,
visibly and at a specific sequence number.

---

## Verifying a recording

Use `check_determinism`. It is the self-test for a tape.

```python
report = agenttape.check_determinism("runs/x.tape", run_agent, repeats=3)
assert report.deterministic, report.problems
```

It replays *n* times and confirms that every pass:

1. consumes every recorded event (nothing skipped, nothing ran short),
2. produces the same outcome fingerprint,
3. produces the same state fingerprints in the same order,
4. raises no divergence.

A tape that fails is not a usable recording. **The fix is to route the offending
boundary through the session, not to loosen the check.**

### What a leak looks like

```python
def leaky_agent(session):
    jitter = random.random()                     # not recorded
    answer = session.model("m", {...}, fn=...)
    session.outcome({"answer": answer})
```

```python
>>> sorted(agenttape.Tape.describe("leaky.tape")["counts"])
['model', 'outcome', 'record']                   # no 'random' event at all
```

The recording has no idea the value existed. It is not that replay gets the wrong
value — replay gets *a* value, from a fresh `random.random()`, which may or may
not match. That is the worst kind of bug: probabilistic.

Note that this is not a contrived mistake. It is what any helper library that
calls `random`, `time`, `datetime.now`, `uuid.uuid4`, or `os.environ` directly
looks like from the outside.

---

## Finding the boundaries

There is no automatic way to enumerate the non-deterministic boundaries of a
Python program. What you can do is look for the call sites.

```bash
# Time
grep -rn "time\.\|datetime\.\|date\.today" src/ | grep -v agenttape

# Randomness
grep -rn "random\.\|secrets\.\|uuid\." src/ | grep -v agenttape

# Environment
grep -rn "os\.environ\|os\.getenv\|getcwd\|platform\." src/

# Identity and ordering
grep -rn "id(\|hash(\|\.items()\|set(" src/
```

Anything that shows up and is not routed through a session is a potential leak.

---

## The completeness ceiling

`agenttape` can only replay what it recorded. Concretely, these are holes:

| Hole | Why |
|---|---|
| A bare `requests.get` / `httpx` client | Not wrapped, so it executes for real during replay |
| A subprocess | Not wrapped; its output is not recorded |
| A database driver | Not wrapped |
| A C extension reading entropy | Not wrapped, and not interceptable from Python |
| `id()`, `hash()`, unordered set iteration | Values depend on process state |
| Thread or process scheduling | No canonical interleaving |

The library's response is to fail loudly at the first call it cannot match, rather
than to paper over the gap. But **it cannot tell you that a hole exists** — only
that it failed to reproduce something. A tape with a hole that happens to produce
the same values on replay will pass `check_determinism` and still be incomplete.

This is open problem #1 in the
[research brief](../research/deterministic-replay-and-rollback-for-ai-agents.md):
a sound completeness check does not exist. Anyone who claims otherwise is
overstating their tooling.

---

## Practical guidance

**Wrap at the outermost layer you control.** Wrapping a provider SDK's client
object catches every call the agent makes through it. Wrapping individual helper
functions is more precise and easier to get wrong.

**Record the request completely.** Replay cannot detect a change it was not told
about. If the answer depends on the model version, put the model version in the
request. If it depends on a system prompt, put the system prompt in the request.
A fingerprint over an incomplete request is a fingerprint that will not notice.

**Record the failure path.** A tool that raised is the most interesting event in
the run. Wrap the call so the exception is recorded, not swallowed before it
reaches the session.

**Call `check_determinism` in CI.** A tape that used to replay cleanly and no
longer does is telling you something changed — in the agent, in a dependency, or
in the environment.

**Prefer many small tapes to one big one.** A tape per scenario is a test suite. A
tape per hour of production traffic is an archive nobody reads.

---

## Related

- [Design](design.md) — how the recording and matching actually work.
- [Tape format](tape-format.md) — the on-disk specification.
- [Research](../research/deterministic-replay-and-rollback-for-ai-agents.md) — the
  evidence and the open problems.
