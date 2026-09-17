#!/usr/bin/env python
"""Break the library on purpose, and check that the tests notice.

Coverage tells you which lines *execute*. It does not tell you which lines are
*protected*. A line can be 100% covered and completely untested at the same time,
if the only test that reaches it walks the happy path through it.

This script applies a curated mutation to the source, runs the suite with ``-x``,
restores the file, and reports whether the suite failed. A mutation the suite does
not catch is a gap: the behaviour it breaks is not actually asserted anywhere.

It is deliberately **not** part of ``make check`` or CI. Nine mutations at roughly
ten seconds each is a couple of minutes, which is fine for an occasional audit and
wrong for every push. Run it when you change something load-bearing:

    python tools/mutation_check.py
    make mutation

Every mutation below corresponds to a claim the documentation makes. If one
survives, either the claim is untested or the code no longer honours it -- both
are worth knowing.
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: (label, relative path, anchor, replacement)
#:
#: The anchor must appear exactly once in the file, so that a refactor which
#: moves the code makes this script report a skip rather than mutate the wrong
#: thing.
MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "durability: drop the fsync before returning",
        "src/agenttape/tape.py",
        "        handle.flush()\n        os.fsync(handle.fileno())",
        "        handle.flush()",
    ),
    (
        "hash chain: stop covering the predecessor",
        "src/agenttape/events.py",
        '    return sha256_hex(canonical_dumps(body).encode("utf-8"))',
        "    return sha256_hex(\n"
        '        canonical_dumps({k: v for k, v in body.items() if k != "prev"}).encode("utf-8")\n'
        "    )",
    ),
    (
        "replay: tolerate a mismatched request instead of raising",
        "src/agenttape/channel.py",
        "        if expected is not None and expected.matches(kind, name, key):",
        "        if expected is not None and (expected.matches(kind, name, key) or True):",
    ),
    (
        "replay: serve None instead of the recorded response",
        "src/agenttape/session.py",
        '        if event.status == "error":\n'
        "            raise rebuild_error(event.error or {})\n"
        "        return event.response",
        '        if event.status == "error":\n'
        "            raise rebuild_error(event.error or {})\n"
        "        return None",
    ),
    (
        "rollback: compensate oldest-first instead of newest-first",
        "src/agenttape/session.py",
        "            for entry in reversed(self._effects)",
        "            for entry in self._effects",
    ),
    (
        "overwrite guard: delete a plain file again (the 0.1.1 bug)",
        "src/agenttape/tape.py",
        "        if target.is_symlink():\n            raise refuse(",
        "        if target.is_file():\n            target.unlink()\n            return\n"
        "        if target.is_symlink():\n            raise refuse(",
    ),
    (
        "verify: report a tampered tape as intact",
        "src/agenttape/verify.py",
        "            recomputed = event_hash(body)\n            if recomputed != declared:",
        "            recomputed = declared\n            if recomputed != declared:",
    ),
    (
        "counts: include the footer in the manifest counts",
        "src/agenttape/tape.py",
        "            if event.kind == EventKind.FOOTER:\n                continue",
        "            if False:\n                continue",
    ),
    (
        "canonical: stop sorting set elements",
        "src/agenttape/canonical.py",
        "        items.sort(key=canonical_dumps)",
        "        pass",
    ),
]


def run_suite(timeout: int) -> bool:
    """Return True if the suite *detected* a problem."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests", "-x", "-q", "-p", "no:cacheprovider"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return True  # a hang is a detection too
    return result.returncode != 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--timeout", type=int, default=300, help="seconds per mutation")
    parser.add_argument("--only", help="substring filter on the mutation label")
    args = parser.parse_args()

    selected = [m for m in MUTATIONS if not args.only or args.only in m[0]]
    if not selected:
        print("no mutations matched {!r}".format(args.only))
        return 2

    dirty = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(ROOT), capture_output=True, text=True
    ).stdout.strip()
    if dirty:
        print("working tree is dirty; refusing to mutate. Commit or stash first.")
        print(dirty)
        return 2

    caught, survived = [], []
    for label, relpath, anchor, replacement in selected:
        path = ROOT / relpath
        original = path.read_text(encoding="utf-8")
        if original.count(anchor) != 1:
            print("SKIP      {} (anchor appears {} times)".format(label, original.count(anchor)))
            continue

        # newline="" so restoring does not rewrite every line ending.
        path.write_text(original.replace(anchor, replacement, 1), encoding="utf-8", newline="")
        try:
            detected = run_suite(args.timeout)
        finally:
            path.write_text(original, encoding="utf-8", newline="")

        (caught if detected else survived).append(label)
        print("{:<9} {}".format("CAUGHT" if detected else "SURVIVED", label))

    total = len(caught) + len(survived)
    print()
    print("caught {} of {}".format(len(caught), total))
    if survived:
        print()
        print("SURVIVORS -- the suite does not protect these:")
        for label in survived:
            print("  -", label)
        print()
        print("Add a test that fails for the mutated behaviour, or delete the")
        print("mutation if the behaviour is not actually a promise.")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
