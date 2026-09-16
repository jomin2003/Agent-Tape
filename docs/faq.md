# FAQ

## Is this a replacement for observability tooling?

No, and it is not trying to be. Observability answers "how is my agent behaving
across many runs?" Replay answers "what exactly happened in this one run, and can
I make it happen again?"

The two have different shapes. Observability is an *outbound* pipe: the agent
emits spans. Replay needs an *inbound* pipe: the environment feeds recorded
answers back into the agent. A tracing SDK cannot serve a response.

The data overlaps heavily, so if you already collect traces, enabling
replay-grade capture is mostly a question of **completeness** — monitoring may
sample or truncate; replay cannot.

## Why not just use `temperature=0` and a seed?

Because neither is a guarantee. OpenAI's `seed` parameter explicitly does not
promise reproducibility, and `system_fingerprint` tracks configuration without
guaranteeing that identical fingerprints produce identical output. Anthropic
documents non-determinism at `temperature=0.0`.

The root cause is batch sensitivity: the same request lands in differently sized
batches on a shared inference fleet, kernel tiling and reduction partitioning
change with batch size, and the resulting ULP-scale differences flip argmax at
roughly 1–5% of token positions. Once a token flips, generation diverges
autoregressively.

The evidence: Qwen3-235B-A22B, 1000 runs at `temperature=0`, **80 distinct
outputs**.

See [the research brief](../research/deterministic-replay-and-rollback-for-ai-agents.md).

## Does replay make real API calls?

No. In replay mode the callable you pass to a recorded boundary is never invoked.
Replaying a tape that recorded a refund does not issue a refund. There is a test
that asserts exactly this.

## Is replay a sandbox?

No, and this distinction matters. "The tool is never invoked" is a property of
this implementation, not a verifiable guarantee about your process. If you need
replay to be *provably* side-effect free, run it with no network access and a
read-only filesystem, and treat the tape as untrusted input.

## What happens if my agent changes?

Replay diverges at the first call that does not match, and tells you which one and
what changed:

```
agenttape.errors.RequestMismatchError: replay diverged at seq 2: the agent's call
does not match the recording
  expected #2 model:gpt-4o {"messages":[{"content":"one"}],"temperature":0.0}
  observed model:gpt-4o (same call, different arguments)
    --- recorded
    +++ replayed
    @@ -1,4 +1,4 @@
      {
    -  "content": "one"
    +  "content": "TWO"
      }
```

That is the feature. Replay-as-regression-test is the most underrated use of the
library: record a golden run, change the agent, replay, and find out exactly where
behaviour moved — instead of finding out when a user reports it.

## My refactor legitimately reordered some calls. Can I still replay?

Yes, with `strict=False`. The engine searches the tape for a matching request
(bounded by `lookahead`) and records each divergence in `session.divergences`.
`session.verified` goes false, because it is not a faithful replay.

This is not the default, because tolerance is precisely the property you do not
want while debugging.

## How do I know my tape is complete?

You do not, and neither does the library. This is open problem #1 in the research
brief: there is no sound static or runtime check for "is every non-deterministic
boundary wrapped?"

What you can do:

- `check_determinism(path, run)` replays several times and confirms the tape is
  self-consistent. It catches most leaks, because a leak usually produces a
  different value on a later pass.
- Audit call sites for `time`, `random`, `secrets`, `uuid`, `os.environ`,
  `os.getcwd`, `id()`, `hash()`, and unordered iteration. See
  [Determinism](determinism.md) for the grep commands.
- Remember the failure mode: a hole that happens to produce the same values on
  replay will pass and still be incomplete.

Anyone who claims a completeness check exists is overstating their tooling.

## Does it work with LangChain / LlamaIndex / CrewAI / my framework?

It is framework-agnostic by construction: it has no dependencies and no
integration layer. You wrap the boundaries yourself, wherever your agent actually
talks to the outside world.

```python
@session.tool_fn("vector_search")
def vector_search(query, k=5):
    return index.similarity_search(query, k=k)
```

Because the session is just an object you pass around, it composes with anything —
including a framework's own checkpointing, which solves a different problem
(durability, not reproducibility).

## How big are tapes?

A step with a 2–4K-token context and a few-hundred-token response is roughly
10–15 KB raw; tool payloads add 1–5 KB each. A 10-step run is typically
100–200 KB. At cloud storage prices that is pennies per million runs.

The expensive tail is documents and images. A 50-page PDF flowing through several
steps can produce a multi-megabyte trace — which is what the blob store is for:
the payload is stored once, content-addressed, and referenced by digest.

## Are tapes safe to share?

**No, not without checking.** A tape records full prompts, full model responses,
and the complete contents of any file read through `Session.read_text`. If your
prompts contain personal data or credentials, so does the tape.

If you attach a tape to a bug report, look at it first. `blobs/` holds the large
payloads, and `events.jsonl` holds everything else.

## Can I replay a tape recorded by a different version?

Within the same `format_version`, yes. The manifest carries `format_version` and
readers refuse versions they do not understand rather than guessing. Unknown
event kinds and unknown event fields are tolerated rather than causing a parse
failure.

Tapes are meant to outlive the library, so format breaks are treated as a last
resort and will be called out in the changelog.

## Can I replay against a model that has been deprecated?

You can replay the *recorded* responses forever — that is the point of a tape.
What you cannot do is **counterfactual** replay against a model version that no
longer exists: you cannot ask "what would GPT-4 have said to this new prompt?"
once GPT-4 is gone.

For long-horizon compliance, the tape is the permanent record, not a reproducible
experiment.

## What about concurrent or streaming agents?

Out of scope for now, and stated as such in the README. Streaming chunk boundaries
depend on network scheduling, and concurrent tool calls have no canonical order.
Both are open problems in the field, and pretending otherwise would be worse than
declaring the boundary.

If your agent issues concurrent tool calls, record them sequentially (await each
before starting the next) and the tape will be faithful.

## Why does `rollback()` need compensators registered by name?

Because the tape must be replayable in a process where the original callable does
not exist. A name in the tape plus a registration in the replaying process is the
only portable arrangement.

Note that replay never *calls* the compensator, so you only need to register one
if you intend to record.

## Does `rollback()` guarantee the effect was undone?

No. There is no general theory of "how undone" an effect is, and compensating
transactions are new actions with their own failure modes. `rollback()` returns a
per-effect record with `ok` and either `result` or `error`, so a partial failure is
visible rather than silent.

See open problem #4 in the research brief.

## Why is there no CLI?

Because it is a library. A CLI is a separate package that imports this one, and
there is a test asserting that no console entry point is declared. If you want
one, the public API is designed to make it a thin layer.

## Why are there no dependencies?

A replay engine sits *underneath* the agent. Every dependency it adds is a
dependency the agent inherits, and every dependency is a potential source of
non-determinism. Standard library only, and there is a test that enforces it.

## How do I contribute?

See [CONTRIBUTING.md](../CONTRIBUTING.md). The two rules that matter most: no
runtime dependencies, and replay never guesses.
