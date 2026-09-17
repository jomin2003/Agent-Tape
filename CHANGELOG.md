# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

While the version is `0.x`, the public API may change between minor releases.
Changes that break the tape format will bump `format_version` in the manifest and
will be called out explicitly below; tapes are meant to outlive the library that
wrote them, so format breaks are treated as a last resort.

## [Unreleased]

### Added

- `tools/mutation_check.py`, plus a `make mutation` target. It applies a curated
  defect to the source, runs the suite, restores the file, and reports whether
  the suite noticed. Coverage says which lines *execute*; this says which lines
  are *protected*. The first run found two promises that nothing tested:

  - **Durability.** Deleting the `fsync` before `append` returns left the suite
    green, even though the README and `docs/design.md` both make the guarantee
    load-bearing — it is why a crash leaves a truncated log rather than a holed
    one. There are now tests that spy on `os.fsync`.
  - **Manifest counts.** Including the footer in `counts` was also undetected:
    the existing assertion checked `counts["tool"] == 1`, which stays true when
    the footer is wrongly added.

  All nine mutations are caught now. The tool is not part of `make check` or CI —
  two minutes is right for an occasional audit and wrong for every push.

## [0.1.1] - 2026-09-17

A correctness pass driven by measuring test coverage. Nothing here changes the
tape layout, so `format_version` stays at 1.

### Fixed

- **`Tape.create(path, overwrite=True)` deleted a plain file without a word.**
  The overwrite guard was written to refuse anything that is not a tape, and its
  docstring said so, but its first branch unlinked *any* file or symlink it
  found. A path that pointed at a file rather than a directory — a typo, or a
  misread argument — was destroyed and replaced by a tape directory. Files and
  symbolic links are now refused with an explanation, like non-tape directories
  already were.
- **A recorded `frozenset` replayed as a `set`.** Both used the same tag, so the
  distinction was lost at encode time. Replay hands decoded responses back to the
  agent, so a value that came back as a `set` would break any code using it as a
  dict key or a set member. They now have distinct tags, which means
  `canonical_dumps(set)` and `canonical_dumps(frozenset)` are no longer equal —
  a stricter, and more correct, comparison.
- **`exception` decoded differently from `dataclass`.** The `dataclass` tag was
  preserved through decoding; the `exception` tag was unwrapped to its bare error
  record, losing the fact that the value had been an exception. Both now keep
  their tag.
- **Counterfactual tapes were labelled as ordinary recordings.** `Session.close`
  set the `"mode": "counterfactual"` summary inside its *replay* branch, but
  going live flips the session's mode to `"record"`, so a forked session never
  reached it. The label was never written at all, and the block was unreachable
  dead code. It now lives in the branch that actually runs.
- **`Tape.describe` did not validate the manifest.** It returned whatever
  `json.loads` produced, so a manifest containing `[]` made `Tape.open` fail with
  `AttributeError: 'list' object has no attribute 'get'` instead of a clear
  format error. Both now share one validated reader.
- The `canonical` module docstring claimed the encoding was "a genuine
  round-trip for every type the encoder understands". That was untrue for
  `dataclass` and `exception`, which decode to plain data rather than to the
  original object — deliberately, since rebuilding them would mean importing a
  class named by the tape. The docstring now says which tags invert exactly and
  which do not.

### Removed

- `channel.describe_request`. It was never called, never exported, and had no
  tests: dead code with a plausible-looking docstring.

### Added

- 79 tests, taking the suite from 276 to 355, and coverage from 94% to 99%.
  They cover the canonical round-trip for every tag, `Tape`'s sequence protocol
  and convenience readers, malformed and truncated tapes, damaged blob stores,
  the full inherited `random.Random` surface, and the divergence-reporting paths
  that only fire on unusual input.
- A coverage floor of 95% in `pyproject.toml`, enforced by a CI job. A floor
  rather than a target: it catches a new module or branch arriving with no tests,
  not a defensive guard going unexercised. Guards that genuinely cannot be
  reached are marked `# pragma: no cover` with a comment saying why.

### Changed

- `README.md` no longer tells you to `pip install agenttape`. The package is not
  on PyPI, so anyone following it got a 404. It installs from the tag, with the
  checkout route for development. `CONTRIBUTING.md` said `cd agenttape` where the
  repository is `Agent-Tape`, which failed on the second line of the clone
  sequence.
- `docs/index.md` was orphaned — nothing linked to it. The README now has a
  Documentation table covering every doc.

## [0.1.0] - 2026-09-17

First public release.

The two defects under **Fixed** below were found by the CI matrix during the
first push, before this version had been published anywhere. They are part of
0.1.0 rather than a follow-up patch, which is why there is no 0.1.1.

### Added

- `Session.record` / `Session.replay` and the `agenttape.record` /
  `agenttape.replay` helpers, with a `with`-statement API.
- `Tape`: append-only, hash-chained, `fsync`-before-return event log, with a
  separately readable `manifest.json` and a content-addressed blob store for
  oversized payloads.
- Recorded boundaries: model calls, tool calls, wall and monotonic clock, random
  number generation, environment reads, file reads, UUIDs, tokens, state
  snapshots, outcomes, marks and logs.
- Sequence-primary, fingerprint-validated replay matching. Divergence raises
  `RequestMismatchError` with a unified diff of the recorded and replayed
  requests. `TapeExhaustedError` and `UnconsumedEventsError` cover runs that are
  longer or shorter than the recording.
- `Session.effect` / `Session.compensator` / `Session.rollback`: compensation
  plans recorded in the tape, so rollback is itself replayable and never
  re-executes a compensator during replay.
- `Tape.fork` plus `Session.replay(..., on_exhausted="live", fork_to=...)` for
  counterfactual replay: branch a recording, replay the prefix, continue live
  into a new tape, leaving the original untouched.
- `verify`, `diff`, and `check_determinism` for integrity checking, regression
  diffing, and tape self-testing.
- Canonical serialisation and fingerprinting that are stable across processes
  and `PYTHONHASHSEED` values.
- Zero runtime dependencies; pure standard library.
- Tests covering the full inherited `random.Random` surface (`gauss`,
  `triangular`, `betavariate`, `expovariate`, `gammavariate`, `lognormvariate`,
  `normalvariate`, `paretovariate`, `weibullvariate`, `randbytes`) against
  record and replay. The design claim is that overriding `random()` and
  `getrandbits()` captures every method, including ones added to the standard
  library after this code was written.
- `test_integrity_and_payload_availability_fail_independently`, which pins the
  reason the event hash covers the body *as stored* rather than the logical one:
  tamper detection must not depend on payloads being resolvable.

### Fixed

- **`DeterministicRandom` raised `TypeError` on Python 3.9 and 3.10.** Any agent
  that touched `session.rng` was unusable on those versions.

  `random.Random` is a C type, and before Python 3.11 its `__new__` inspects the
  positional argument tuple itself:

  ```c
  if (PyTuple_GET_SIZE(args) > 1) {
      PyErr_SetString(PyExc_TypeError, "Random() requires 0 or 1 argument");
      return NULL;
  }
  ```

  `DeterministicRandom(session, seed)` therefore failed inside `tp_new`, before
  `__init__` ever ran — with two arguments the call is rejected outright, and
  with one it would have tried to *seed with the session object*. The class now
  defines `__new__` and drops the arguments, so zero positional arguments reach
  the C constructor, which is the one case it has always accepted.

  Two further consequences of not calling `random.Random.__init__` were fixed at
  the same time: `gauss_next` is now initialised explicitly (`gauss()` reads it
  directly and raised `AttributeError` on Python 3.11+), and the constructor is
  covered by a test that passes both arguments.

  The argument check was removed in 3.11, so a modern interpreter cannot
  reproduce the failure. Caught by the CI matrix; verified against
  `Modules/_randommodule.c` on the 3.10 branch.
- The third-party-dependency guard in the test suite was vacuous on Python 3.9,
  where `sys.stdlib_module_names` does not exist: it reported every standard
  library import as third-party and failed for the wrong reason. The check now
  falls back to asking where each module resolves on disk, and a meta-test
  asserts that the guard can actually detect a dependency.

### Changed

- The `package` CI job now installs the built wheel into a clean environment and
  runs the whole suite against it, with `src/` not importable. This catches a
  wheel that imports but is missing a module — and it caught exactly that class
  of mistake in a new test the first time it ran.
- Narrowed the Ruff rule selection to a correctness-focused set (`E`, `F`, `W`,
  `I`, `B`, `C4`, `SIM`, `RUF`) and dropped `UP`, `N`, `PTH`, `ARG`, `RET` and
  `TCH`. Their findings here were stylistic preferences rather than defects, and
  `UP` would have rewritten ~200 `str.format()` calls into f-strings for no
  functional gain.
- `mypy` no longer pins `python_version = "3.9"`. mypy has dropped the ability to
  target 3.9 while this package still supports it, so pinning it would make the
  config contradict `requires-python`. Python 3.9 compatibility is guarded by the
  3.9 job in the CI matrix.
- Resolved all `mypy` findings, including a shadowed parameter name in
  `check_determinism` and untyped `Tape._fh`.

[Unreleased]: https://github.com/jomin2003/Agent-Tape/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/jomin2003/Agent-Tape/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/jomin2003/Agent-Tape/releases/tag/v0.1.0
