"""The public API surface, and the constraints that define this project.

These tests guard properties that are easy to break by accident and expensive to
notice later: a stray third-party import, a console script, a public name that
does not exist, an undocumented callable.
"""

from __future__ import annotations

import ast
import importlib.util
import os
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

#: Present from Python 3.10. Empty on 3.9, which is why the path check exists.
STDLIB_NAMES = set(getattr(sys, "stdlib_module_names", ()))

#: Directories that hold installed distributions rather than the standard library.
THIRD_PARTY_MARKERS = ("site-packages", "dist-packages")

PACKAGE_DIR = pathlib.Path(at.__file__).parent


def _is_third_party(root):
    """Return ``True`` if *root* resolves to an installed distribution.

    ``sys.stdlib_module_names`` does not exist before Python 3.10, so the
    fallback asks where the module actually lives. That path check is the more
    robust test anyway: it works inside a virtualenv, where the standard library
    and ``site-packages`` share a prefix.
    """
    if root in STDLIB_NAMES or root in sys.builtin_module_names:
        return False
    try:
        spec = importlib.util.find_spec(root)
    except (ImportError, ValueError, AttributeError):
        return False
    if spec is None or spec.origin in (None, "built-in", "frozen"):
        return False
    origin = os.path.abspath(spec.origin)
    return any(os.sep + marker + os.sep in origin + os.sep for marker in THIRD_PARTY_MARKERS)


def _imported_roots(path):
    """Return the top-level module names imported by a source file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_runtime_has_no_third_party_dependencies():
    """A replay engine sits under the agent; every dependency it adds is inherited."""
    external = {}
    for path in sorted(PACKAGE_DIR.glob("*.py")):
        foreign = sorted(
            root
            for root in _imported_roots(path)
            if root != "agenttape" and not root.startswith("_") and _is_third_party(root)
        )
        if foreign:
            external[path.name] = foreign
    assert external == {}, "third-party imports found: {}".format(external)


def test_the_dependency_check_can_actually_detect_a_dependency(tmp_path):
    """Guard against the check silently going vacuous.

    It did exactly that on Python 3.9, where ``sys.stdlib_module_names`` does not
    exist: every stdlib import was reported as third-party and the test failed
    for the wrong reason. A guard that cannot fail is not a guard.
    """
    module = tmp_path / "fake.py"
    module.write_text("import json\nimport os\nimport pytest\n", encoding="utf-8")

    assert _imported_roots(module) == {"json", "os", "pytest"}
    assert _is_third_party("pytest")
    assert not _is_third_party("json")
    assert not _is_third_party("os")
    assert not _is_third_party("sys")
    # agenttape is excluded by *name* in the check above, not by location: from
    # a wheel it resolves inside site-packages, and from a checkout inside src/.
    assert not _is_third_party("a_module_that_does_not_exist")


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
