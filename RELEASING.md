# Releasing

A checklist. Everything here is mechanical; the point is not to skip a step.

## Before you publish the repository

The scaffolding uses placeholder values in a few places. Replace them **before**
the first public push:

| File | Placeholder | Replace with |
|---|---|---|
| `pyproject.toml` | `https://github.com/your-org/agenttape` (×4 in `[project.urls]`) | The real repository URL |
| `pyproject.toml` | `authors = [{ name = "agenttape contributors" }]` | A real name and, optionally, an email |
| `NOTICE` | `https://github.com/your-org/agenttape` | The real repository URL |
| `CITATION.cff` | `repository-code`, `url` | The real repository URL |
| `CHANGELOG.md` | `your-org` in the link definitions | The real organisation |
| `CONTRIBUTING.md` | `git clone https://github.com/your-org/agenttape.git` | The real clone URL |

Then:

- [ ] `LICENSE` is present and correct (Apache-2.0), and `NOTICE` names the
      copyright holder.
- [ ] `agenttape.__version__` and the git tag agree.
- [ ] `git log` contains no secrets, no tapes, and no personal data. Tapes are
      gitignored, but check anyway — they contain full prompts.
- [ ] `python -m build && python -m twine check dist/*` passes.
- [ ] The CI workflow is green on every supported Python and OS.
- [ ] The examples all run offline: `make examples`.
- [ ] The README renders correctly on GitHub, including the tables and the
      `research/` link.
- [ ] The repository description and topics are set on GitHub.
- [ ] `SECURITY.md` is present so GitHub surfaces the private reporting flow.

## Release checklist

1. **Confirm the suite is green on every supported version.**

   ```bash
   make check
   ```

2. **Update the changelog.** Move everything under `## [Unreleased]` into a new
   `## [x.y.z] - YYYY-MM-DD` section, and update the comparison links at the
   bottom.

3. **Bump the version** in `src/agenttape/_version.py`:

   ```python
   __version__ = "x.y.z"
   VERSION_INFO = (x, y, z)
   ```

   `pyproject.toml` reads the version from this file, so there is exactly one
   place to change.

   **If you changed the on-disk format**, also bump `TAPE_FORMAT_VERSION`, add a
   row to the version history in `docs/tape-format.md`, and say so loudly in the
   changelog. Tapes are meant to outlive the library; a format break is a last
   resort.

4. **Commit and tag.**

   ```bash
   git add -A
   git commit -m "release: vx.y.z"
   git tag -a vx.y.z -m "agenttape vx.y.z"
   ```

5. **Build and inspect.**

   ```bash
   make build
   tar tzf dist/agenttape-x.y.z.tar.gz | head -40
   python -m zipfile -l dist/agenttape-x.y.z-py3-none-any.whl
   ```

   Confirm the wheel contains `agenttape/`, `agenttape/py.typed`, and nothing
   that should not be shipped (no tests, no examples, no `.tape` directories).

6. **Install the wheel into a clean environment and smoke-test it.**

   ```bash
   python -m venv /tmp/smoke
   /tmp/smoke/bin/pip install dist/agenttape-x.y.z-py3-none-any.whl
   /tmp/smoke/bin/python -c "import agenttape; print(agenttape.__version__)"
   ```

7. **Push the tag.** Releases are cut from tags; the tag is the source of truth.

   ```bash
   git push origin main
   git push origin vx.y.z
   ```

8. **Verify the push actually landed.** A silently failed push looks exactly like
   a successful one.

   ```bash
   git status -sb
   git ls-remote origin refs/tags/vx.y.z
   ```

9. **Upload to PyPI.**

   ```bash
   python -m twine upload dist/*
   ```

   Or, preferably, publish through a trusted-publisher workflow so no API token
   is handled locally.

10. **Create the GitHub release** from the tag, using the changelog section as
    the body.

11. **Verify the install from PyPI.**

    ```bash
    python -m venv /tmp/pypi-smoke
    /tmp/pypi-smoke/bin/pip install agenttape==x.y.z
    /tmp/pypi-smoke/bin/python -c "import agenttape; print(agenttape.__version__)"
    ```

12. **Post-release**: add a fresh `## [Unreleased]` heading to the changelog and
    commit it.

## Versioning policy

[Semantic Versioning](https://semver.org/). While the major version is `0`, the
public API may change between minor releases, and the changelog will say so.

Two things are treated as breaking even in `0.x`:

- **Removing or renaming anything in `agenttape.__all__`.** There is a test that
  the list is well-formed, so the surface is deliberate.
- **Changing the on-disk format.** Bumps `TAPE_FORMAT_VERSION`, needs a migration
  note, and is a last resort.

Adding new `EventKind` values is *not* breaking: readers tolerate unknown kinds by
design.
