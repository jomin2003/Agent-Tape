"""The public API surface, and the constraints that define this project.

These tests guard properties that are easy to break by accident and expensive to
notice later: a stray third-party import, a console script, a public name that
does not exist, an undocumented callable.
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys

import pytest

import agenttape as at


# --------------------------------------------------------------------------- #
# Surface
# --------------------------------------------------------------------------- #


def test_every_public_name_is_importable():
    missing = [name for name in at.__all__ if not hasattr(at, name)]
    assert missing == []


def test_no_public_name_is_exported_twice():
    assert len(at.__all__) == len(set(at.__all__))


def test_public_names_are_sorted_and_unique():
    assert at.__all__ == list(dict.fromkeys(at.__all__))


def test_version_is_pep440():
    assert re.match(r"^\d+\.\d+\.\d+([.-].+)?$", at.__version__)


def test_version_matches_version_info():
    assert at.__version__.split(".")[:3] == [str(part) for part in at.VERSION_INFO]


def test_format_version_is_an_int():
    assert isinstance(at.TAPE_FORMAT_VERSION, int)


def test_documented_error_hierarchy():
    assert issubclass(at.DivergenceError, at.AgentTapeError)
    assert issubclass(at.RequestMismatchError, at.DivergenceError)
    assert issubclass(at.TapeExhaustedError, at.DivergenceError)
    assert issubclass(at.UnconsumedEventsError, at.DivergenceError)
    assert issubclass(at.TapeFormatError, at.TapeError)
    assert issubclass(at.TapeIntegrityError, at.TapeError)


# --------------------------------------------------------------------------- #
# Documentation
# --------------------------------------------------------------------------- #

PUBLIC_CALLABLES = [
    "record",
    "replay",
    "verify",
    "diff",
    "check_determinism",
    "canonical_dumps",
    "canonical_loads",
    "to_canonical",
    "from_canonical",
    "fingerprint",
    "sha256_hex",
    "short_hash",
    "rebuild_error",
    "request_diff",
]


@pytest.mark.parametrize("name", PUBLIC_CALLABLES)
def test_public_functions_are_documented(name):
    assert getattr(at, name).__doc__, "{} has no docstring".format(name)


PUBLIC_CLASSES = [
    "Session",
    "Tape",
    "Event",
    "EventKind",
    "DeterministicClock",
    "DeterministicRandom",
    "Recorder",
    "Replayer",
    "TapeReport",
    "TapeDiff",
    "DeterminismReport",
]


@pytest.mark.parametrize("name", PUBLIC_CLASSES)
def test_public_classes_are_documented(name):
    assert getattr(at, name).__doc__, "{} has no docstring".format(name)


@pytest.mark.parametrize("name", PUBLIC_CLASSES)
def test_public_classes_have_documented_public_methods(name):
    cls = getattr(at, name)
    undocumented = [
        attribute
        for attribute in dir(cls)
        if not attribute.startswith("_")
        and callable(getattr(cls, attribute))
        and not getattr(getattr(cls, attribute), "__doc__", None)
    ]
    assert undocumented == [], "{}.{} undocumented".format(name, undocumented)


def test_module_docstring_explains_the_limits():
    """The README-level honesty should also be in the package docstring."""
    docstring = at.__doc__ or ""
    assert "not" in docstring.lower()
    assert "deterministic" in docstring.lower()


# --------------------------------------------------------------------------- #
# Project constraints
# --------------------------------------------------------------------------- #

STDLIB = set(getattr(sys, "stdlib_module_names", ()))
PACKAGE_DIR = pathlib.Path(at.__file__).parent


def _imported_roots(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_runtime_has_no_third_party_dependencies():
    """A replay engine sits under the agent; every dependency it adds is inherited."""
    external = {}
    for path in sorted(PACKAGE_DIR.glob("*.py")):
        roots = _imported_roots(path)
        foreign = {
            root
            for root in roots
            if root not in STDLIB and root != "agenttape" and not root.startswith("_")
        }
        if foreign:
            external[path.name] = sorted(foreign)
    assert external == {}, "third-party imports found: {}".format(external)


def test_there_is_no_console_entry_point():
    """agenttape is a library, not a CLI or an application."""
    pyproject = PACKAGE_DIR.parent.parent / "pyproject.toml"
    if not pyproject.is_file():  # installed from a wheel
        pytest.skip("pyproject.toml not available")
    tomllib = pytest.importorskip("tomllib", reason="needs Python 3.11+")
    with open(pyproject, "rb") as handle:
        data = tomllib.load(handle)
    project = data.get("project", {})
    assert "scripts" not in project
    assert "gui-scripts" not in project
    assert "entry-points" not in project


def test_there_is_no_main_module():
    assert not (PACKAGE_DIR / "__main__.py").exists()


def test_package_is_typed():
    assert (PACKAGE_DIR / "py.typed").is_file()


def test_metadata_files_exist():
    root = PACKAGE_DIR.parent.parent
    if not (root / "pyproject.toml").is_file():
        pytest.skip("running from an installed wheel")
    for name in (
        "README.md",
        "LICENSE",
        "NOTICE",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "CODE_OF_CONDUCT.md",
        "CITATION.cff",
        "pyproject.toml",
    ):
        assert (root / name).is_file(), "missing {}".format(name)


def test_readme_covers_the_required_topics():
    root = PACKAGE_DIR.parent.parent
    readme = root / "README.md"
    if not readme.is_file():
        pytest.skip("running from an installed wheel")
    text = readme.read_text(encoding="utf-8").lower()
    for topic in ("install", "usage", "design", "deterministic", "license", "api"):
        assert topic in text, "README does not mention {}".format(topic)


def test_license_is_apache_2():
    root = PACKAGE_DIR.parent.parent
    license_file = root / "LICENSE"
    if not license_file.is_file():
        pytest.skip("running from an installed wheel")
    text = license_file.read_text(encoding="utf-8")
    assert "Apache License" in text
    assert "Version 2.0" in text
