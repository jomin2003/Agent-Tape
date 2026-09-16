# Releasing

A checklist. Everything here is mechanical; the point is not to skip a step.

## Repository facts

| | |
|---|---|
| Repository | <https://github.com/jomin2003/Agent-Tape> |
| Visibility | Public |
| Default branch | `main` |
| Package name | `agenttape` (the repository name and the import name differ deliberately; `Agent-Tape` is the project, `agenttape` is the module) |
| License | Apache-2.0 |

The repository URLs in `pyproject.toml`, `NOTICE`, `CITATION.cff`, `CHANGELOG.md`,
`CONTRIBUTING.md` and `.github/ISSUE_TEMPLATE/config.yml` all point at the real
repository. If the repository is ever renamed or transferred, update those, then
re-check that `python -m build` still succeeds.

### Open items before a PyPI release

- [ ] `pyproject.toml` `authors` currently lists `agenttape contributors` with no
      email. Add a real name and, if you want one published, an email.
- [ ] `NOTICE` names `agenttape contributors` as the copyright holder. Change it
      to a real name or entity if you want the copyright attributed to one.
- [ ] Decide whether to claim the `agenttape` name on PyPI. It is unclaimed as of
      writing; the first `twine upload` takes it.

### Verified at first publication

- [x] `LICENSE` present and correct (Apache-2.0, verbatim from apache.org), and
      `NOTICE` present.
- [x] `python -m build && python -m twine check dist/*` passes on both sdist and
      wheel; the wheel contains only the package, `py.typed`, and the licenses.
- [x] 273 tests pass; all five examples run offline.
- [x] `git log` contains no secrets, no tapes, and no files over 100 KB.
- [x] The CI workflow covers Python 3.9–3.13 on Linux, macOS and Windows.
- [x] `SECURITY.md` is present so GitHub surfaces the private reporting flow.

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
