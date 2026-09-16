# Security Policy

## Reporting a vulnerability

Please report suspected vulnerabilities privately, using GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
on this repository. Do not open a public issue for a security problem.

Please include:

- a description of the issue and its impact,
- a minimal reproduction,
- the affected version, Python version and platform,
- any suggested remediation.

You can expect an acknowledgement, and an assessment of severity and scope.

## Threat model

`agenttape` writes files and reads them back. That is the whole surface. It opens
no sockets, starts no processes, and imports nothing outside the standard
library. Even so, a few properties are worth stating plainly, because tapes are
data that crosses trust boundaries — a tape is often produced on one machine and
replayed on another, or attached to a bug report.

### What the library protects

**Tape integrity.** Events form a hash chain; each event's hash covers its body
including the previous event's hash. `agenttape.verify` detects edits, reorderings
and truncation in the middle of a log. Blobs are content-addressed, so their
contents are checked against the digest that references them.

**No implicit execution during replay.** In replay mode, the callable passed to a
recorded boundary is never invoked. Replaying a tape that recorded a refund does
not issue a refund. This is enforced in one place (`Replayer`) rather than being
left to each call site.

**No implicit imports.** When replaying a recorded exception,
`agenttape.events.rebuild_error` resolves the exception class only from modules
that are *already imported*. A hostile tape cannot cause an import.

### What the library does not protect against

**A hostile tape is still hostile data.** An attacker who can write to your tape
directory can write any payloads they like. Treat tapes with the same care as the
prompts and tool outputs they contain. The hash chain makes tampering *detectable*
by anyone who has a trusted copy of the manifest digest; it does not make
tampering impossible, and `verify` is only meaningful if you call it.

**Tapes contain secrets.** A tape records full prompts, full model responses, and
the complete contents of any file read through `Session.read_text`. If your
prompts contain personal data or credentials, so does the tape. Use the `blobs/`
directory and your filesystem permissions deliberately, and apply your retention
policy to tape directories.

**Unwrapped boundaries are invisible.** The library can only record calls routed
through a session. A bare `requests.get`, a subprocess, or a database driver
reading directly is a hole: it will execute for real during replay, and the
library will not notice. This is the single most important operational caveat —
see the design notes in `docs/determinism.md`.

**Replay is not a sandbox.** "The tool is never invoked" is a property of this
implementation, not a verifiable guarantee about the process. If you need replay
to be provably side-effect free, run it with no network access and a read-only
filesystem, and treat the tape as untrusted input.

## Supported versions

Fixes are applied to the latest release. While the version is `0.x`, only the
most recent minor release is supported.
