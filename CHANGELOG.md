# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

While the version is `0.x`, the public API may change between minor releases.
Changes that break the tape format will bump `format_version` in the manifest and
will be called out explicitly below; tapes are meant to outlive the library that
wrote them, so format breaks are treated as a last resort.

## [Unreleased]

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

### Added

- Tests covering the full inherited `random.Random` surface (`gauss`,
  `triangular`, `betavariate`, `expovariate`, `gammavariate`, `lognormvariate`,
  `normalvariate`, `paretovariate`, `weibullvariate`, `randbytes`) against
  record and replay — the design claim is that overriding `random()` and
  `getrandbits()` captures every method, including ones added to the standard
  library after this code was written.
- `test_integrity_and_payload_availability_fail_independently`, which pins the
  reason the event hash covers the body *as stored* rather than the logical one:
  tamper detection must not depend on payloads being resolvable.

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

## [0.1.0] - 2026-09-17

Initial release.

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

[Unreleased]: https://github.com/jomin2003/Agent-Tape/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/jomin2003/Agent-Tape/releases/tag/v0.1.0
