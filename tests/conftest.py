"""Shared fixtures.

Every test writes tapes into pytest's ``tmp_path``, so the suite never touches
the working tree and never leaves state behind between runs.
"""

from __future__ import annotations

import os

import pytest

import agenttape as at


@pytest.fixture
def tape_path(tmp_path):
    """A path inside the test's temporary directory, not yet created."""
    return str(tmp_path / "run.tape")


@pytest.fixture
def src_dir():
    """Filesystem path of the ``src`` directory, for subprocess-based tests."""
    return os.path.dirname(os.path.dirname(os.path.abspath(at.__file__)))


@pytest.fixture
def recorded(tmp_path):
    """A small recorded run, returned as ``(tape_path, agent_callable)``.

    The agent makes one tool call, one model call, records a state and an
    outcome -- enough to exercise ordering, matching and completion checks.
    """

    def agent(session):
        hits = session.tool("search", ["refund policy"], fn=lambda: ["doc-a", "doc-b"])
        answer = session.model(
            "gpt-4o",
            {"messages": [{"role": "user", "content": hits}]},
            fn=lambda: "Refunds within 30 days.",
        )
        session.state({"step": 1}, label="after-model")
        session.outcome({"answer": answer})
        return answer

    path = str(tmp_path / "recorded.tape")
    with at.record(path) as session:
        agent(session)
    return path, agent
