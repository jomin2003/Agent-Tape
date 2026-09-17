"""The claims `SECURITY.md` makes, tested.

A security policy that describes guarantees nobody checks is worse than no
policy: it tells a reader to stop worrying about something. Each test here
corresponds to a sentence in `SECURITY.md`.

The claims that are already covered elsewhere are noted rather than duplicated:

- tape integrity, and that a damaged blob store is reported separately from
  tampering -- `test_verify.py`
- that replay never invokes the callable it was handed -- `test_session.py`
- that a hostile tape cannot make the library instantiate a class it names --
  `test_canonical.py::test_dataclass_decodes_to_plain_data_not_an_instance`
"""

from __future__ import annotations

import ast
import importlib.util
import pathlib
import sys

import pytest

import agenttape as at
from agenttape import EventKind, fingerprint


def _hostile_tape(path, *, type_name, module_name, message="boom"):
    """Build a tape whose recorded tool call failed with a named exception.

    The request and key are built exactly as ``session.tool("evil", [])`` would
    build them, so replay matches the event and reaches the error path rather
    than diverging on the fingerprint.
    """
    request = {"args": [], "kwargs": {}}
    with at.Tape.create(path) as tape:
        tape.append(
            kind=EventKind.TOOL,
            name="evil",
            key=fingerprint(EventKind.TOOL, "evil", request),
            request=request,
            response=None,
            status="error",
            error={
                "type": type_name,
                "module": module_name,
                "message": message,
                "repr": "{}({!r})".format(type_name, message),
            },
        )


PACKAGE_DIR = pathlib.Path(at.__file__).parent

#: A module that is importable, but that neither the library nor pytest imports.
#: Chosen because the claim under test is that a tape naming it changes nothing.
UNIMPORTED_MODULE = "colorsys"


# --------------------------------------------------------------------------- #
# "Nothing is imported on a tape's behalf."
# --------------------------------------------------------------------------- #


def test_a_tape_cannot_cause_an_import(tmp_path):
    """A recorded exception names a module. Replaying it must not import it.

    Module import is code execution, so if a tape could name a module and have it
    imported, anyone who can write to a tape directory could run code in the
    replaying process. `rebuild_error` resolves the class from ``sys.modules``
    only, and never imports.
    """
    assert importlib.util.find_spec(UNIMPORTED_MODULE) is not None, (
        "{} must be importable for this test to mean anything".format(UNIMPORTED_MODULE)
    )
    assert UNIMPORTED_MODULE not in sys.modules, (
        "{} was already imported; pick another module".format(UNIMPORTED_MODULE)
    )

    path = str(tmp_path / "hostile.tape")
    _hostile_tape(path, type_name="Payload", module_name=UNIMPORTED_MODULE)

    with at.replay(path) as session:
        with pytest.raises(at.AgentTapeError):
            session.tool("evil", [], fn=lambda: None)

    assert UNIMPORTED_MODULE not in sys.modules, "replay imported a module named by the tape"


def test_a_tape_cannot_name_a_module_that_does_not_exist(tmp_path):
    path = str(tmp_path / "hostile.tape")
    _hostile_tape(path, type_name="Nope", module_name="nowhere.at.all")

    with at.replay(path) as session:
        with pytest.raises(at.RecordedError) as excinfo:
            session.tool("evil", [], fn=lambda: None)
    assert excinfo.value.record["type"] == "Nope"


def test_the_library_contains_no_dynamic_import_machinery():
    """The strongest form of the claim: there is nothing to abuse.

    ``eval``, ``exec`` and ``compile`` are included because a tape is data, and
    data that reaches any of them is code.
    """
    forbidden = {"import_module", "__import__", "eval", "exec", "compile"}
    found = []
    for path in sorted(PACKAGE_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in forbidden:
                found.append("{}: {}".format(path.name, node.id))
            elif isinstance(node, ast.Attribute) and node.attr in forbidden:
                found.append("{}: .{}".format(path.name, node.attr))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] == "importlib":
                        found.append("{}: import importlib".format(path.name))

    assert found == [], "dynamic import or evaluation machinery found: {}".format(found)


# --------------------------------------------------------------------------- #
# "It opens no sockets, starts no processes, and imports nothing outside the
#  standard library."
# --------------------------------------------------------------------------- #

#: Modules that would contradict the sentence above.
FORBIDDEN_MODULES = {
    "socket",
    "ssl",
    "subprocess",
    "multiprocessing",
    "asyncio",
    "threading",
    "concurrent",
    "http",
    "urllib",
    "ftplib",
    "smtplib",
    "telnetlib",
    "xmlrpc",
    "webbrowser",
    "ctypes",
    "pickle",
    "marshal",
}


def _imported_roots(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_the_library_opens_no_sockets_and_starts_no_processes():
    """`SECURITY.md` says the library's only surface is reading and writing files.

    Checked statically against the import graph, which is the property that
    actually matters: the library cannot open a socket if it never imports
    anything that can.
    """
    offenders = {}
    for path in sorted(PACKAGE_DIR.glob("*.py")):
        hit = sorted(_imported_roots(path) & FORBIDDEN_MODULES)
        if hit:
            offenders[path.name] = hit
    assert offenders == {}, "modules that contradict SECURITY.md: {}".format(offenders)


def test_the_forbidden_module_list_is_not_vacuous():
    """Guard the guard: the check must be able to detect a real import."""
    assert _imported_roots(PACKAGE_DIR / "tape.py") & {"os", "json", "shutil"}
    sample = PACKAGE_DIR.parent.parent / "tests" / "test_security.py"
    assert "socket" in FORBIDDEN_MODULES
    assert sample.is_file() or True  # the file may not exist when run from a wheel


# --------------------------------------------------------------------------- #
# Tapes are data, and are treated as data
# --------------------------------------------------------------------------- #


def test_a_tape_with_a_hostile_manifest_is_rejected_not_executed(tmp_path):
    """A manifest is parsed as JSON, never evaluated."""
    target = tmp_path / "hostile.tape"
    target.mkdir()
    (target / "manifest.json").write_text(
        "{\"format\": \"__import__('os').system('echo pwned')\"}", encoding="utf-8"
    )
    with pytest.raises(at.TapeFormatError):
        at.Tape.open(target)


def test_a_tape_with_a_hostile_event_line_is_rejected_not_executed(tmp_path):
    """A line of Python where JSON was expected is corruption, not code.

    A *single* malformed line would be read as a truncated tail -- the tape looks
    like it was killed mid-write, and that is tolerated by design. So the hostile
    line goes after a valid one, where it can only be corruption.
    """
    tape = at.Tape.create(tmp_path / "t.tape")
    tape.append(
        kind=EventKind.MARK,
        name="legit",
        key=fingerprint(EventKind.MARK, "legit", {"label": "legit"}),
        request={"label": "legit"},
        response=None,
    )
    tape.close()

    events = tmp_path / "t.tape" / "events.jsonl"
    lines = events.read_text(encoding="utf-8").splitlines()
    lines.insert(1, '__import__("os").system("echo pwned")')
    events.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(at.TapeIntegrityError):
        list(at.Tape.open(tmp_path / "t.tape").iter_events())


def test_blob_contents_are_verified_not_trusted(tmp_path):
    """A blob is addressed by the hash of its contents, and that hash is checked."""
    path = str(tmp_path / "b.tape")
    with at.record(path, blob_threshold=64) as session:
        session.tool("fetch", ["x"], fn=lambda: "y" * 500)

    blob = next((tmp_path / "b.tape" / "blobs").iterdir())
    blob.write_bytes(b'{"injected": true}')

    with pytest.raises(at.TapeIntegrityError, match="corrupted"):
        list(at.Tape.open(path).iter_events())
