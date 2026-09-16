# Deterministic Replay and Rollback for AI Agents

### A research brief on a named, still-unsolved systems problem — and the design constraints it imposes on tooling

**Status:** literature review + problem statement
**Last updated:** 2026-09-17
**Scope:** why agent runs cannot be reproduced, why rollback is harder than checkpointing, what the state of the art actually achieves, and what remains open.

---

## 1. Executive summary

An AI agent run is, in the general case, **not reproducible**. Re-execute the same
program against the same task and you will usually get a different trajectory — a
different tool call order, a different plan, a different answer. This is not a bug in
any one framework. It is a structural property of a system whose control flow is
produced by a stochastic sampler that reads a mutable outside world.

The consequences are concrete and expensive:

- **You cannot debug what you cannot reproduce.** A production failure that cannot be
  re-executed is a story, not a defect.
- **You cannot regression-test a prompt.** A prompt edit that fixes one scenario and
  silently breaks five others is invisible to unit tests, because there is no
  deterministic unit.
- **You cannot safely retry.** A retried agent that already issued a refund issues it
  twice.
- **You cannot audit.** Regulated decisions must be explainable after the fact, and
  "the model said something different that time" is not an explanation.

The systems community solved the general shape of this problem decades ago under the
name **record/replay** (deterministic replay). The AI-agent community has, since
roughly 2025, been rediscovering it — and has not yet solved it. The core difficulty is
that classical record/replay assumes the *recording* and the *replay* see the same
world, whereas an agent's world (model weights, hosted inference fleets, live APIs,
the clock, the filesystem) is neither frozen nor versioned.

This brief argues that **deterministic replay and rollback for AI agents is a named
unsolved problem with two halves**:

1. **Replay half** — reproducing a past run faithfully, offline, without re-executing
   side effects. *Largely solved in the single-process, synchronous case; unsolved in
   the concurrent, streaming, multi-agent case.*
2. **Rollback half** — undoing the effects of a run. *Not solved at all in the general
   case, because tool side effects are arbitrary and non-invertible.*

The library in this repository (`agenttape`) is a deliberately narrow attack on the
first half plus the *replayable* part of the second: it makes the recording itself the
unit of truth, so that rollback is expressed as a recorded, replayable sequence of
compensations rather than an improvised one.

---

## 2. The problem statement

### 2.1 What "deterministic replay" means here

Given an agent program `P`, a task `T`, and a recorded run `R`, deterministic replay
means: **re-executing `P` on `T` produces exactly the same observable trajectory as
`R`, with no live calls to models, tools, the network, the clock, or the random number
generator.**

"Observable trajectory" is deliberately narrower than "internal state". Replay does not
give you the model's hidden reasoning; it gives you the *sequence of boundary
crossings* — every prompt sent, every response received, every tool invoked, every
clock read, every random draw — replayed in the same order.

### 2.2 Why it is hard: the non-determinism is layered

Non-determinism in an agent is not one thing. It is at least seven distinct mechanisms
stacked on top of each other, and eliminating any single one is insufficient.

| # | Layer | Mechanism | Can a library fix it? |
|---|-------|-----------|----------------------|
| 1 | **Sampling** | Temperature/top-p/top-k select among near-tied tokens. | No — must be *recorded*. |
| 2 | **Numerical (batch sensitivity)** | Kernel outputs depend on batch size; argmax flips on ~ULP-scale differences. | No — must be *recorded*. |
| 3 | **Hardware heterogeneity** | Mixed GPU fleets implement matmuls with slightly different numerics. | No — must be *recorded*. |
| 4 | **MoE routing** | Unstable top-k expert routing and capacity-driven token dropping. | No — must be *recorded*. |
| 5 | **External state** | Tools return live data: search results, prices, ticket state. | No — must be *recorded*. |
| 6 | **Ambient state** | Clock, RNG, env vars, filesystem, UUIDs, hash-order iteration. | **Yes** — intercept and record. |
| 7 | **Scheduling** | Concurrency, streaming chunk boundaries, retry timing, multi-agent interleaving. | Partly — record the interleaving. |

A replay library can only *control* layers 6 and 7. Layers 1–5 must be **captured**.
This is the single most important design consequence in this whole document: **a
deterministic replay system is, first and foremost, a complete recording system.** Its
correctness ceiling is the completeness of its instrumentation.

### 2.3 The numbers

The evidence that these layers matter is now reasonably strong.

- **Greedy decoding is not deterministic.** Qwen3-235B-A22B, run 1000 times on the same
  input with `temperature=0`, produced **80 distinct outputs**, the modal output
  appearing only 78 times.
- **The cause is batch sensitivity, not thread scheduling.** Single GPU + fixed batch
  size produces bit-identical outputs; the divergence appears when the *same request
  lands in differently-sized batches*. Kernel tiling and reduction partitioning change
  with batch size, so `K(X)[i] ≠ K'(x_i)` — the batch-invariance property fails in
  practice.
- **Per-kernel error compounds.** Per-kernel relative differences of `10⁻⁶`–`10⁻⁸` over
  `L` layers give `ε_total ≈ L·ε`; for `L = 80`, `ε_total ≈ 8×10⁻⁶`. Roughly **1–5% of
  token positions** are "fragile" — the top-2 logits are closer than `ε_total` — and each
  such position is a coin flip. Divergence then compounds autoregressively.
- **Vendors say so explicitly.** OpenAI's `seed` parameter "improves" but does not
  guarantee reproducibility; `system_fingerprint` tracks configuration but identical
  fingerprints still do not guarantee identical output. Anthropic documents that
  results are not fully deterministic even at `temperature=0.0`.
- **Aggregate impact is large.** Across five LLMs configured for deterministic output,
  observed accuracy varied by up to **15%** between runs, with a best-to-worst spread
  of **70%**.
- **Compounding is the real killer.** In a multi-step agent, step *n*'s output is step
  *n+1*'s input. A 1% per-step divergence rate is not 1% over ten steps; the runs become
  "unrecognizable" by step 10.

The engineering fix for layers 1–3 exists — enforce fixed RMSNorm reduction
partitioning, fixed matmul tiling, fixed attention KV split size, and deterministic
all-reduce — and it works. It costs **~30–60% throughput** (vLLM 1000 → 385 tok/s for
TML `batch_invariant_ops`; ~30% for FlashInfer with fixed split size; ~25% for
FlashAttention-3; ~40% for Triton). SGLang + CUDA Graphs lands at 657 tok/s, a ~34%
overhead. The trade is often worth it for a different reason than determinism:
variance collapses (P50 45→52 ms, but P99 120→58 ms and stddev 25→3 ms).

**This is a server-side fix, not a client-side one.** A client-side library cannot make
someone else's inference endpoint batch-invariant. It can only record what came back.

---

## 3. Why existing observability does not solve it

The market is full of agent observability platforms (Langfuse, LangSmith, Arize,
Braintrust, and others). They are not replay systems, and the gap is not incidental:

| Property | Observability platforms | Deterministic replay |
|---|---|---|
| Purpose | Understand aggregate behaviour | Reproduce one specific run |
| Capture fidelity | Sampled, truncated, redacted, async | Verbatim, complete, synchronous |
| Direction | Agent → collector (telemetry) | Collector → agent (substitution) |
| Replay | Not a capability | The entire capability |
| Failure mode | "We didn't log that field" | "Replay diverged at seq 5" |

Two structural mismatches matter most:

1. **Direction.** Observability is an *outbound* pipe: the agent emits spans. Replay
   requires an *inbound* pipe: the environment feeds recorded answers back into the
   agent. A tracing SDK cannot serve a response.
2. **Completeness.** Sampling and truncation are reasonable for dashboards and fatal for
   replay. A replay engine needs *every* boundary, *verbatim* — including the ones that
   errored, timed out, or returned partial results, because those are frequently the
   interesting ones.

The corollary is that enabling replay-grade tracing is cheap for teams already running
observability: the data overlaps heavily. The delta is completeness, not new plumbing.

---

## 4. The record/replay primitive

### 4.1 What must be captured

Per boundary crossing:

- **Model calls** — full prompt as sent, all sampling parameters (temperature, top_p,
  max_tokens, stop, seed), model identifier *and version/fingerprint*, and the exact
  response, including finish reason and token accounting.
- **Tool calls** — name, arguments, complete result, *and* errors, timeouts, and partial
  results.
- **Ambient state** — clock reads, random draws, environment variables read, files
  read, IDs generated.
- **Decision metadata** — routers, planners, classifiers: their inputs and outputs as
  first-class events, not as log lines.
- **State** — agent state snapshots, or at least fingerprints of them, at chosen
  boundaries.

### 4.2 Bookkeeping requirements

- A **monotonically increasing sequence number** per event.
- A **run/tape identifier** for correlation.
- A **structured, append-only, streamable** on-disk format. JSONL is the pragmatic
  choice: append-only, human-readable, diffable, trivially streamable, and requiring no
  database.
- A **format version** on every tape, so an old tape can still be read by a newer
  engine.
- **Write-before-return durability.** The event must be on disk (fsync'd) *before* the
  agent acts on the response. Otherwise a crash produces a *holed* log — an event
  recorded but never acted on, or acted on but never recorded — which is worse than a
  truncated one. A truncated log is recoverable; a holed log is a lie.

### 4.3 Matching semantics: the crux

When replay asks "what was the response to this call?", the engine must decide how to
find it. Two families:

- **Sequence-primary.** The *n*-th replay call must equal the *n*-th recorded event.
  Strongest correctness guarantee — any deviation is a real divergence. Fails on
  legitimate reordering.
- **Key-indexed.** Match on a fingerprint of `(kind, name, request)`. Tolerates
  reordering and concurrency, but can silently serve a plausible-but-wrong response if
  the agent is *nearly* deterministic — which is exactly the situation in which you
  most need to be told the truth.

The defensible design is **sequence-primary with fingerprint validation**: compare
positionally, and additionally verify the request fingerprint so the error message can
say *what* changed, not merely *that* something did. Never fall through to a live
system silently. A replay engine that guesses is worse than no replay engine, because it
manufactures false confidence.

### 4.4 Full replay vs. checkpoint replay

| | Full replay | Checkpoint replay |
|---|---|---|
| Method | Replay every step from the start | Snapshot state at step boundaries; jump to any point |
| Strength | Complete path; small storage | Interactive debugging; fork and inject |
| Cost | Slow for long runs (50 steps to inspect step 47) | Checkpoints can be megabytes each |

The recommended hybrid: **always** store the complete event log (compact), and store
full state checkpoints only for runs that failed, alerted, or were flagged. LangGraph's
"time travel" is a production instance of the checkpoint approach: every state
transition is persisted and can be forked.

### 4.5 The storage math

- A step with a 2–4K-token context and a few-hundred-token response ≈ **10–15 KB** raw.
- Tool payloads add **1–5 KB** each.
- A 10-step run ≈ **100–200 KB**.
- At cloud storage prices this is "pennies per million runs". The real cost is
  infrastructure: indexing, retention, and the replay engine itself.
- The expensive tail is documents and images. A 50-page PDF flowing through several LLM
  calls can produce a multi-megabyte trace. The fix is **content-addressed reference
  recording**: store the blob once, record its hash in the event, deduplicate across
  runs.

---

## 5. State of the art

| System | Year | Approach | Notable result | Boundary |
|---|---|---|---|---|
| **`rr` / Mozilla rr** (general systems) | 2011– | Record/replay for native processes via syscall interception | Bit-exact process replay | Not aware of agents, LLMs, or non-invertible side effects |
| **FoundationDB deterministic simulation** | 2010s | Single-threaded simulated network + injected time/randomness | Entire clusters simulated deterministically in one process | Requires the system be *written* deterministically |
| **Antithesis** | 2010s– | Deterministic simulation + fault injection at hypervisor level | Reproducible "impossible" bugs | Needs full environment control |
| **Temporal** | 2017– | Durable execution: replay workflow code from an event history | Workflows resume exactly after crashes | Requires *workflow determinism* — the developer must not use wall-clock or RNG directly; non-determinism is a hard error |
| **AgentRR** (arXiv:2505.17716) | 2025 | Record user traces → *summarise* into a generalised "experience" → replay with check functions as a TCB | Bounded-intelligence reuse; multi-level experience | Explicitly trades bit-fidelity for generalisation; "100% reliable replay" called elusive |
| **agrepl** (arXiv:2607.16200) | 2026 | MITM proxy at the transport layer; structured traces; replay with zero outbound network | **Replay fidelity F = 1.0** over 5 workloads / n = 250; **98.3% median per-step latency reduction** | Transport-layer only; Go binary; sees HTTP, not in-process state |
| **agentrr** (OSS) | 2026 | In-process record/replay of LLM + tool + clock/RNG/ID boundaries | Crash-safe `fsync`-before-return; halts on divergence with diffs | Single-process, synchronous agents only |

### 5.1 What the state of the art proves

Three things are now established:

1. **Faithful replay is achievable** in the single-process, synchronous, request/response
   case. agrepl reports fidelity 1.0; agentrr reports CI-verified boundary-sequence
   equality. The engineering is tractable.
2. **Replay is dramatically cheaper than re-execution.** 98.3% median per-step latency
   reduction in agrepl — because a tape read replaces a network round trip. Replay is
   not just for debugging; it is a fast path.
3. **Divergence detection is the product.** The valuable output is not "here is the
   recording" but "your code changed behaviour at step 5, here is the diff".

### 5.2 What it does not prove

- Concurrency, streaming-chunk replay, and multi-agent pipelines are explicitly out of
  scope in every current implementation.
- Transport-layer interception (agrepl) cannot see in-process state: a direct
  `datetime.now()` call or an in-process tool is invisible to a proxy.
- In-process interception (agentrr) cannot see anything the agent does outside its
  wrapped boundaries — a raw `requests.get`, a database driver, a subprocess.
- Neither can replay against a *deprecated* model for counterfactual purposes. Recorded
  traces remain readable, but you cannot ask "what would GPT-4 have said to this new
  prompt?" once GPT-4 is gone.

---

## 6. Rollback: the second, harder half

Replay tells you what happened. **Rollback** is about undoing it. It is a different
problem, and it is less solved.

### 6.1 Why rollback is not "restore a snapshot"

An agent's effects live in the outside world: a payment moved, an email sent, a ticket
closed, a file deleted, a row inserted. You cannot restore those by rewinding a process
image. And you usually *cannot invert* them:

- Sending an email has no inverse (the recipient has read it).
- A payment can be refunded, but the refund is a *new* transaction, not an undo — it
  leaves a trace, may fail, may be partially applied.
- Deleting a file is invertible only if you kept the bytes.

This is the classic **saga** problem from distributed transactions: replace one atomic
transaction with a sequence of local transactions, each paired with a **compensating
transaction** that semantically undoes it. Compensations are *not* rollbacks: they are
new actions with their own failure modes, and they must be **idempotent**, because
retries are guaranteed.

### 6.2 Why agents make it worse

Three agent-specific amplifiers:

1. **The set of effects is not known statically.** The agent chooses its tools at
   runtime. You cannot pre-declare the compensation for a step that has not been planned
   yet.
2. **Effects are chosen by a stochastic planner.** A retry does not merely repeat the
   last action; it may choose a *different* one, so compensation must cover a branch it
   never observed.
3. **The failure is often semantic, not technical.** A tool call can succeed and still
   be wrong. Retrying after a *semantic* failure is precisely the "duplicated side
   effect" scenario: the first call already did the thing.

The failure mode this produces is the one practitioners fear most: **retry amplification**
— the system's error handling is what causes the damage.

### 6.3 The mitigations that actually work

- **Idempotency keys** on every mutating tool call, derived from the *logical* action,
  not the attempt. This converts "exactly once" into "at least once + deduplication",
  which is achievable.
- **Transactional outbox**: write the intent to a durable store in the same local
  transaction as the state change, then deliver asynchronously. Makes "recorded" and
  "done" agree.
- **Durable checkpointing** of agent state before each effectful step.
- **Reconciliation / state diffing** rather than blind retry when a call succeeds but
  business state is inconsistent. Stop retrying; compare intended vs. actual state.
- **Compensation registries**: each effect declares, at the time it is recorded, how it
  would be compensated. The declaration lives in the *tape*, not in the planner's head.

That last point is the design insight this library exploits: **if the tape already
records every effect, then the tape is the natural place to record how to undo each
effect — and rollback becomes another replayable run.**

---

## 7. Why it is still unsolved: open problems

These are the named, still-open problems. Each is stated as a falsifiable claim.

1. **The completeness problem.** *A replay engine can only replay what it recorded.*
   Any unwrapped path — a raw HTTP client, a subprocess, a C extension reading entropy —
   is a hole. There is no way today to *prove* that a tape is complete, only to fail to
   find a hole. **Open:** a sound static or runtime completeness check.

2. **The concurrent/streaming problem.** Every production implementation scopes itself
   to single-process, synchronous, request/response agents. Streaming chunk boundaries
   are not reproducible (they depend on network scheduling), and concurrent tool calls
   have no canonical order. **Open:** a canonical interleaving semantics for concurrent
   and streaming agent execution.

3. **The model-deprecation problem.** Recorded responses replay forever, but
   *counterfactual* replay ("what would the new model have said?") dies with the model
   version. Long-horizon compliance therefore depends on traces, not on reproducibility.
   **Open:** model-version-pinned counterfactual replay.

4. **The rollback completeness problem.** There is no general mechanism to guarantee
   that a compensating action has fully undone an effect. Partial compensation is the
   norm and there is no theory of "how undone" an effect is. **Open:** a compositional
   semantics for compensation adequacy.

5. **The cross-agent problem.** A2A/MCP topologies mean one logical run spans several
   processes, owners, and trust domains. Whose tape is authoritative? How do you
   correlate them? **Open:** a distributed tape format with causal ordering.

6. **The side-effect-free proof problem.** Replay must be *provably* side-effect free.
   "We didn't call the tool" is an implementation claim, not a verifiable property.
   **Open:** verifiable isolation guarantees for replay (sandboxes with attested
   no-network).

7. **The tape-is-a-liability problem.** Tapes contain full prompts and responses,
   frequently including personal data. Verbatim capture is in direct tension with data
   minimisation and retention law. Redaction breaks replay fidelity. **Open:**
   replay-preserving redaction.

8. **The divergence-semantics problem.** When replay diverges, is that a bug in the
   agent (good signal), a legitimate code change, or benign reordering? Today the engine
   halts and a human decides. **Open:** principled divergence classification and
   tolerances.

9. **The cost problem.** Bit-deterministic inference costs 30–60% throughput. Tapes cost
   storage and governance. There is no accepted accounting for "the price of
   reproducibility" per workload. **Open:** an economic model for replayability.

10. **The benchmark problem.** There is no standard corpus of agent runs on which replay
    engines can be compared on fidelity, completeness, and cost. agrepl's F = 1.0 over
    n = 250 is a good start and is not yet a benchmark. **Open:** a shared
    replay-fidelity benchmark.

A reasonable position: **problems 1, 2, 4, and 6 are the load-bearing ones.** Until they
have answers, deterministic replay for agents will remain a well-understood engineering
practice with a weak theoretical foundation — which is, historically, roughly where
process record/replay sat before `rr`.

---

## 8. Design implications adopted by `agenttape`

The research above is not decoration; it constrains the library. `agenttape` is
deliberately scoped to the part of the problem that is *soluble today*, and honest about
the rest.

| Finding | Design decision in `agenttape` |
|---|---|
| Layers 1–5 cannot be controlled, only recorded | The tape is the unit of truth; there is no "make it deterministic" mode, only "capture it faithfully" |
| A wrong guess is worse than a loud failure | **Sequence-primary, fingerprint-validated** matching. Divergence raises by default and reports expected-vs-observed |
| Write-before-return, or the log lies | `fsync` on every event append, before the value is returned to the agent |
| Content-addressed storage for the expensive tail | Blobs above a size threshold are stored once, referenced by SHA-256 |
| Version the format | `format_version` in the manifest of every tape |
| Replay must be provably side-effect-free | In replay mode the wrapped callable is *never invoked*; recorded exceptions are re-raised |
| Rollback is a recorded, replayable action | `effect(..., compensate=...)` records compensations in the tape; `rollback()` replays them in reverse |
| Checkpoint replay enables counterfactual debugging | `Tape.fork()` + `on_exhausted="live"` lets a replay run past its recording and continue into a new tape |
| Unwrapped paths are holes | The library documents the boundary loudly rather than pretending to be complete |
| Concurrency/streaming is unsolved | Out of scope, stated explicitly |

What `agenttape` explicitly **does not** claim: that it can make an arbitrary agent
deterministic; that it can undo arbitrary side effects; that it can see calls it was not
wrapped around; or that it solves any of the ten open problems in §7.

---

## 9. References

Primary sources consulted.

1. Mudasiru, R. *Deterministic Replay for AI Agent Systems.* arXiv:2607.16200, Apr 2026.
   <https://arxiv.org/abs/2607.16200> — agrepl: MITM-proxy record/replay; replay fidelity
   F = 1.0 (n = 250); 98.3% median per-step latency reduction; request-key matching
   function K(s); noise-aware header diff.
2. *Get Experience from Practice: LLM Agents with Record & Replay* (AgentRR).
   arXiv:2505.17716, May 2025. <https://arxiv.org/html/2505.17716v1> — record/summarise/
   replay; multi-level experience; check functions as TCB; generalization-vs-safety
   tension.
3. *Nondeterminism in LLM Inference: Root Cause Analysis and Batch Invariance.*
   <https://phonism.github.io/LLMNotes/en/llm-nondeterminism/> — batch-invariance
   formalism `K(X)[i] = K'(x_i)`; RMSNorm/matmul/attention tiling dependence; 80 distinct
   outputs in 1000 runs; 1–5% fragile token positions; throughput overheads.
4. *Defeating Nondeterminism in LLM Inference.* Thinking Machines, Sep 2025. — batch
   invariance, deterministic kernels, reproducible RL training with SGLang.
5. *Deterministic Replay: How to Debug AI Agents That Never Run the Same Way Twice.*
   <https://tianpan.co/blog/2026/04/12/deterministic-replay-debugging-non-deterministic-ai-agents>
   — taxonomy of divergence sources; storage math; checkpoint vs. full replay; replay as
   regression testing; the four things replay cannot do.
6. *Compensating Transactions and Failure Recovery for Agentic Systems.*
   <https://tianpan.co/blog/2026/03/17/compensating-transactions-failure-recovery-agentic-systems>
   — saga pattern, idempotency keys, durable checkpointing for agents.
7. *Idempotency and Side-Effect Safety in Production AI Agents.*
   <https://www.llms.blog/posts/idempotency-and-side-effect-safety-in-production-ai-agents-transactional-outboxes-distributed-sagas-and-compensating-actions>
   — transactional outbox, sagas, compensating actions.
8. Apple / FoundationDB. *Simulation and Testing.*
   <https://apple.github.io/foundationdb/testing.html> — single-process deterministic
   cluster simulation.
9. Antithesis. *Deterministic simulation testing — how it works and when to use it.*
   <https://antithesis.com/docs/resources/deterministic_simulation_testing/>.
10. Bluehand Research. *Deterministic Replay Architectures for AI Systems*
    (BH-RL-2026-0006). <https://www.blue-hand.org/research/deterministic-replay/> —
    replay as a trust/governance mechanism; explicit boundary against claiming "total
    access to hidden model reasoning".
11. agentrr (OSS). <https://github.com/ip174/agentrr> — in-process deterministic
    record-and-replay debugger; Apache-2.0.
12. Temporal. Durable execution and workflow replay determinism constraints.
13. LangGraph. Persistence, checkpointing, and time travel.

---

## 10. Changelog

- **2026-09-17** — initial brief.
