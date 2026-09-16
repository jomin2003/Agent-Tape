# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

While the version is `0.x`, the public API may change between minor releases.
Changes that break the tape format will bump `format_version` in the manifest and
will be called out explicitly below; tapes are meant to outlive the library that
wrote them, so format breaks are treated as a last resort.

## [Unreleased]

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
