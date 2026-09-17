# Contributing to agenttape

Thanks for considering a contribution. This project is small and deliberately
narrow; the notes below are mostly about keeping it that way.

## Getting set up

```bash
git clone https://github.com/jomin2003/Agent-Tape.git
cd Agent-Tape
python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
```

There is no build step, no code generation, and no service to start. The test
suite runs in a few seconds and writes only into pytest's temporary directory.

## The rules that matter here

### 1. No runtime dependencies

`agenttape` has no third-party runtime dependencies, and `pyproject.toml` should
stay that way. A replay engine sits *underneath* the agent: every dependency it
adds is a dependency the agent inherits, and every dependency is a potential
source of non-determinism. There is a test that enforces this
(`test_runtime_has_no_third_party_dependencies`).

Standard library only. If you think you need a dependency, open an issue first
and make the case.

### 2. It stays a library

No CLI, no console entry points, no `__main__.py`, no server. If you want a CLI,
build it on top of the library in a separate package — that is exactly what the
public API is for. Tests enforce this too.

### 3. Never guess during replay

If replay cannot match a call to the recording, it raises. It does not fall
through to a live system, and it does not silently serve a nearby response. This
is the property that makes the library worth using: a replay engine that guesses
manufactures false confidence, which is worse than no replay engine.

Any change to the matching logic must preserve this. `on_divergence="warn"` is
the escape hatch, and it records what it did.

### 4. Durability before return

`Tape.append` writes, flushes, and `fsync`s before returning the value to the
caller. The agent must never act on a value that is not yet on disk. A truncated
log is recoverable; a *holed* log — an effect that happened with no record of it
— is a lie. Do not reorder this.

### 5. New recorded boundaries need tests on both sides

A new boundary is only real if it is proven to (a) execute and record during
recording, and (b) *not* execute during replay. Look at `tests/test_rollback.py`
for the pattern — the ledger assertion that stays empty is the important one.

## Code style

- Formatting and linting are handled by [Ruff](https://docs.astral.sh/ruff/):
  `ruff format .` and `ruff check .`.
- **Ruff also formats Python code blocks inside Markdown.** That is deliberate —
  it keeps the examples in `README.md` and `docs/` syntactically valid and
  consistently styled. The consequence is that a fenced `python` block is not a
  free-form drawing surface; write code that formats well, and avoid relying on
  manual alignment inside a code fence.
- Line length is 100. Target Python 3.9, so no `match`, no `X | Y` at runtime, no
  `StrEnum`, no `slots=True`.
- Type hints on everything public. `mypy src/agenttape` should stay clean.
- Docstrings explain *why*, not *what*. The code already says what it does; the
  docstring is where the reasoning goes.

## Tests

- Every behaviour change needs a test. Bug fixes need a test that fails before
  the fix.
- Prefer many small, well-named tests over a few large ones.
- Tests must not depend on wall-clock time, network access, or the order in which
  they run.

### Checking that the tests protect anything

Coverage tells you which lines *execute*. It does not tell you which lines are
*protected* — a line can be fully covered and completely untested if the only
test that reaches it walks the happy path through it.

`tools/mutation_check.py` applies a curated defect to the source, runs the suite,
restores the file, and reports whether the suite noticed:

```bash
python tools/mutation_check.py          # all of them, ~2 minutes
python tools/mutation_check.py --only rollback
```

Run it when you change something load-bearing — the tape format, the matching
engine, rollback, or verification. Each mutation corresponds to a promise the
documentation makes. If one survives, either the promise is untested or the code
no longer honours it; both are worth knowing before you push.

It is deliberately not part of `make check` or CI. Two minutes is fine for an
occasional audit and wrong for every push.

## Commits and pull requests

- Conventional commit subjects (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`,
  `chore:`) are appreciated but not enforced.
- Keep a pull request focused on one thing. A refactor plus a feature is two pull
  requests.
- Update `CHANGELOG.md` under `## [Unreleased]` for anything a user would notice.
- If you change the on-disk format, say so loudly in the pull request. Format
  changes bump `TAPE_FORMAT_VERSION`, need a migration note, and are treated as a
  last resort — tapes are meant to outlive the library that wrote them.

## Reporting bugs

The most useful bug report is a tape. If you can reproduce a problem, attach the
tape directory (or a minimal one you built), plus the output of:

```python
import agenttape

print(agenttape.verify("path/to/tape").render())
print(agenttape.Tape.describe("path/to/tape"))
```

**Please check the tape for secrets first.** Tapes record full prompts and
responses, and the contents of any file the agent read.

## Code of conduct

Participation is covered by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
