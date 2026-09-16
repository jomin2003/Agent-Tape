## What this changes

<!-- One or two sentences. What behaviour is different after this pull request? -->

## Why

<!-- The problem this solves. Link an issue if there is one: "Fixes #123". -->

## Checklist

- [ ] Tests added or updated for the change.
- [ ] `make check` passes (lint, types, tests).
- [ ] `CHANGELOG.md` updated under `## [Unreleased]`, if a user would notice.
- [ ] No new runtime dependency (`test_runtime_has_no_third_party_dependencies`).
- [ ] No CLI, server, or console entry point (`test_there_is_no_console_entry_point`).

## Does this affect the guarantees?

Tick anything this touches, and explain below.

- [ ] **Replay matching.** Replay must never serve a response that does not match
      the recording, and must never fall through to a live system.
- [ ] **Durability ordering.** Events must be on disk before the caller acts on
      the value.
- [ ] **The hash chain.** What an event hash covers, or how the chain links.
- [ ] **The on-disk format.** If so, `TAPE_FORMAT_VERSION` must be bumped and
      `docs/tape-format.md` updated.
- [ ] **Canonical encoding.** If so, say why the change cannot make fingerprints
      vary between processes.

<!-- Explain any boxes you ticked. -->

## Notes for the reviewer

<!-- Anything non-obvious: a trade-off you made, an alternative you rejected, a
     part you are unsure about. -->
