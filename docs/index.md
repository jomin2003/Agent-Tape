# agenttape documentation

`agenttape` records AI agent runs to local files and replays them offline,
deterministically.

## Start here

- **[README](../README.md)** — what it is, why it exists, installation, usage.
- **[Design](design.md)** — the tape format, the matching engine, and why each
  decision was made the way it was.
- **[Determinism](determinism.md)** — what the library can and cannot make
  reproducible, and how to find the holes.
- **[Tape format](tape-format.md)** — the on-disk specification, versioned
  independently of the library.
- **[API reference](api.md)** — every public name, with signatures.
- **[FAQ](faq.md)** — the questions that come up when you actually use it.
- **[Research](../research/deterministic-replay-and-rollback-for-ai-agents.md)** —
  the literature review the library is built on, including the ten open problems
  it deliberately does not solve.

## The thirty-second version

```python
import agenttape

with agenttape.record("runs/x.tape") as session:
    run_agent(session)  # real calls, real side effects

with agenttape.replay("runs/x.tape") as session:
    run_agent(session)  # same trajectory, offline, free
```

Everything else is detail.
