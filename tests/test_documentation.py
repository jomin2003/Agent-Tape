"""The documentation is part of the product, so it is tested like one.

A README example that does not run is worse than no example: it is the first
thing a new user tries, and it is the only thing they have to judge the project
by. These tests keep the docs honest against the code they describe.

They skip when running from an installed wheel, where the markdown is not
shipped.
"""

from __future__ import annotations

import importlib
import pathlib
import re

import pytest

import agenttape as at

ROOT = pathlib.Path(at.__file__).parent.parent.parent

#: Markdown files that describe the public API.
DOC_FILES = (
    "README.md",
    "docs/index.md",
    "docs/api.md",
    "docs/design.md",
    "docs/determinism.md",
    "docs/faq.md",
    "docs/tape-format.md",
    "examples/README.md",
    "CONTRIBUTING.md",
)

FENCE = re.compile(r"^```(?:python|py)\n(.*?)^```", re.S | re.M)


def _doc(name: str) -> str:
    path = ROOT / name
    if not path.is_file():
        pytest.skip("{} is not available (installed from a wheel)".format(name))
    return path.read_text(encoding="utf-8")


def _python_blocks(text: str) -> list[str]:
    return FENCE.findall(text)


# --------------------------------------------------------------------------- #
# The headline example
# --------------------------------------------------------------------------- #


def test_the_readme_headline_example_runs(tmp_path):
    """The first example in the README, executed verbatim.

    The helpers it assumes -- `search` and `client.chat` -- are stubbed the way a
    reader would stub them. Everything else is copied from the README, so if the
    documented API changes shape, this fails.
    """
    import agenttape

    def search(query):
        return ["policy/refunds.md", "policy/returns.md"]

    class Client:
        def chat(self, hits):
            return "Refunds within 30 days."

    client = Client()

    def run_agent(session):
        hits = session.tool("search", ["refund policy"], fn=lambda: search("refund policy"))
        answer = session.model(
            "gpt-4o",
            {"messages": [{"role": "user", "content": hits}], "temperature": 0.0},
            fn=lambda: client.chat(hits),
        )
        session.outcome({"answer": answer})
        return answer

    path = str(tmp_path / "runs" / "triage.tape")

    # Record: real calls, real side effects.
    with agenttape.record(path) as session:
        recorded = run_agent(session)

    # Replay: the agent's real logic, every answer served from the tape.
    with agenttape.replay(path) as session:
        replayed = run_agent(session)
        assert session.verified

    assert recorded == replayed == "Refunds within 30 days."


def test_the_readme_headline_example_is_still_in_the_readme():
    """Guards against the test above drifting away from the document it copies."""
    readme = _doc("README.md")
    assert 'session.tool("search", ["refund policy"]' in readme
    assert 'session.model(\n        "gpt-4o",' in readme
    assert 'session.outcome({"answer": answer})' in readme


# --------------------------------------------------------------------------- #
# Every reference in the docs resolves
# --------------------------------------------------------------------------- #

#: What a doc snippet calls on, and the object that name should resolve to.
TARGETS = {
    "agenttape": at,
    "at": at,
    "session": at.Session,
    "tape": at.Tape,
}

CALL = re.compile(r"\b(agenttape|at|session|tape)\.([A-Za-z_][A-Za-z0-9_]*)")


def test_every_api_reference_in_the_docs_exists():
    """No doc example may call a method that is not there.

    Scoped to Python code fences, so prose and directory trees are not mistaken
    for code.
    """
    missing = []
    checked = 0
    for name in DOC_FILES:
        for block in _python_blocks(_doc(name)):
            for target, attribute in CALL.findall(block):
                checked += 1
                if not hasattr(TARGETS[target], attribute):
                    missing.append("{}.{} (in {})".format(target, attribute, name))

    assert checked > 30, "the sweep found suspiciously few references: {}".format(checked)
    assert missing == [], "documented API that does not exist: {}".format(missing)


IMPORT = re.compile(r"^\s*from\s+(agenttape[\w.]*)\s+import\s+([^\n#]+)", re.M)


def test_every_name_the_docs_import_exists():
    missing = []
    checked = 0
    for name in DOC_FILES:
        for block in _python_blocks(_doc(name)):
            for module_name, names in IMPORT.findall(block):
                module = importlib.import_module(module_name)
                for raw in names.replace("(", "").replace(")", "").split(","):
                    symbol = raw.strip()
                    if not symbol or symbol == "*":
                        continue
                    checked += 1
                    if not hasattr(module, symbol):
                        missing.append("{}.{} (in {})".format(module_name, symbol, name))

    assert checked > 0
    assert missing == [], "documented imports that do not exist: {}".format(missing)


def test_every_documented_error_name_exists():
    """`docs/api.md` draws the exception hierarchy; every name in it must be real."""
    api = _doc("docs/api.md")
    block = api[api.index("AgentTapeError") : api.index("```", api.index("AgentTapeError"))]
    named = set(re.findall(r"\b([A-Z][A-Za-z]*Error)\b", block))
    assert named, "could not find the error hierarchy in docs/api.md"
    missing = sorted(name for name in named if not hasattr(at, name))
    assert missing == [], "documented errors that do not exist: {}".format(missing)


def test_every_name_in_the_readme_api_table_exists():
    """The README's public API tables are the index; a stale entry misleads."""
    readme = _doc("README.md")
    section = readme[readme.index("## Public API") : readme.index("## Documentation")]
    names = set(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", section))

    # Only names that claim to live at the top level. `EventKind`'s members are
    # documented inside that row and are attributes of the class, not the package.
    candidates = {n for n in names if n in at.__all__ or n.endswith("Error")}
    assert len(candidates) > 20, "the API table sweep found too few names"
    missing = sorted(n for n in candidates if not hasattr(at, n))
    assert missing == [], "the README's API table lists names that do not exist: {}".format(missing)


def test_every_event_kind_the_readme_lists_is_real():
    """The `EventKind` row names constants; each must exist on the class."""
    readme = _doc("README.md")
    row = next(line for line in readme.splitlines() if "Kind constants" in line)
    listed = re.findall(r"`([A-Z_]+)`", row)
    assert listed, "could not find any event kinds in the README row"
    missing = sorted(name for name in listed if not hasattr(at.EventKind, name))
    assert missing == [], "the README lists event kinds that do not exist: {}".format(missing)


# --------------------------------------------------------------------------- #
# Claims about the package
# --------------------------------------------------------------------------- #


def test_the_documented_version_matches_the_package():
    readme = _doc("README.md")
    assert at.__version__ in readme, "the README should state the current version"


def test_the_documented_test_count_matches_reality():
    """The README states a test count. It has drifted twice; now it cannot.

    Asks pytest itself rather than counting `def test_` lines, because a
    parametrised test is one function and many cases.
    """
    import subprocess
    import sys

    readme = _doc("README.md")
    match = re.search(r"(\d+) tests, a few seconds, no network", readme)
    assert match, "could not find the test count in the README"
    documented = int(match.group(1))

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, "pytest --collect-only failed:\n{}".format(result.stderr[-2000:])

    collected = sum(1 for line in result.stdout.splitlines() if "::" in line and "test" in line)
    assert collected > 0, "could not parse the collected test count"
    assert documented == collected, "the README says {} tests but pytest collects {}".format(
        documented, collected
    )
