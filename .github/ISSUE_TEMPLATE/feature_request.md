---
name: Feature request
about: Suggest a capability or a change in behaviour
title: ""
labels: enhancement
assignees: ""
---

## The problem

What are you trying to do that you cannot do today? Describe the situation, not
the solution — the solution is easier to get right once the problem is clear.

## What you have tried

Any workaround you found, and why it is not good enough.

## Proposed behaviour

What you think the library should do. If it changes the public API, sketch the
call site.

```python
import agenttape

with agenttape.record("runs/x.tape") as session:
    ...   # what would you like to be able to write?
```

## Does it fit the scope?

The library is deliberately narrow. Please check against the boundaries in the
README and in
[`research/deterministic-replay-and-rollback-for-ai-agents.md`](../../research/deterministic-replay-and-rollback-for-ai-agents.md):

- [ ] It does not add a runtime dependency.
- [ ] It does not add a CLI, a server, or an application.
- [ ] It does not make replay guess (tolerating a mismatch silently).
- [ ] It is not concurrency, streaming, or multi-agent replay (currently out of
      scope — see the README).

If any of those boxes is unchecked, say why the exception is warranted. That is a
legitimate argument to make; it just needs making.

## Alternatives considered

Other approaches, and why you rejected them.
