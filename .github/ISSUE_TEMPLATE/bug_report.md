---
name: Bug report
about: Something does not behave as documented
title: ""
labels: bug
assignees: ""
---

<!--
Before you paste anything: a tape records full prompts, full model responses,
and the complete contents of any file the agent read. Please check it for
secrets and personal data first.
-->

## What happened

A clear description of the actual behaviour.

## What you expected

A clear description of the expected behaviour.

## Reproduction

The smallest program that shows the problem. If you can, include the agent
function and the session calls around the failing boundary.

```python
import agenttape

def run_agent(session):
    ...
```

## Tape

If the problem is in replay, verification, or diffing, a tape is by far the most
useful thing you can attach — or a minimal one you built:

```python
import agenttape
print(agenttape.verify("path/to/tape").render())
print(agenttape.Tape.describe("path/to/tape"))
```

```
paste the output here
```

## Environment

- `agenttape` version (`python -c "import agenttape; print(agenttape.__version__)"`):
- Python version:
- Operating system:
- Tape `format_version` (from `manifest.json`):

## Anything else

Whether the failure is in recording, replay, verification, rollback, or the
counterfactual fork. Whether it reproduces every time or intermittently.
