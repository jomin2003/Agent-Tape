"""Single source of truth for the package version.

Kept in a module (rather than resolved from installed distribution metadata) so
that ``agenttape.__version__`` works when running from a source checkout.
"""

from __future__ import annotations

__all__ = ["__version__", "VERSION_INFO", "TAPE_FORMAT_VERSION"]

#: PEP 440 version string. Keep in sync with ``pyproject.toml``.
__version__ = "0.1.0"

#: Machine-readable version tuple.
VERSION_INFO = (0, 1, 0)

#: On-disk tape format version. Bump only for breaking changes to the layout
#: described in ``docs/tape-format.md``.
TAPE_FORMAT_VERSION = 1
