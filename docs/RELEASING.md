# Maintainer Release Procedure

Releases are built from the default branch and published as GitHub Releases.
The project is not automatically uploaded to PyPI.

## Prepare

1. Update the version in `pyproject.toml` and
   `src/obsidian_skill_router/__init__.py`.
2. Move relevant entries from `Unreleased` into a dated section in
   `CHANGELOG.md`.
3. Confirm installation instructions and compatibility notes.
4. Run the complete local gate:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests
python -m compileall -q src tests
python -m build
python -m twine check dist/*
```

5. Install the wheel into a fresh virtual environment and verify both entry
   points:

```powershell
python -m venv .\test_artifacts\release-venv
.\test_artifacts\release-venv\Scripts\python.exe -m pip install .\dist\obsidian_skill_router-*.whl
.\test_artifacts\release-venv\Scripts\obsidian-skill-router.exe --version
.\test_artifacts\release-venv\Scripts\python.exe -m obsidian_skill_router --version
```

6. Merge through a reviewed pull request and wait for required CI checks.

## Tag and publish

Create an annotated tag matching the package version:

```bash
git switch master
git pull --ff-only
git tag -a v0.2.0 -m "v0.2.0"
git push origin v0.2.0
```

The `Release` workflow reruns tests, builds wheel and source distributions in a
read-only job, verifies the tag against the installed package version, and then
passes only the validated artifacts to a separate write-scoped job.

After completion, verify the release assets, source archives, installation from
the Git tag, default-branch protection, private vulnerability reporting, and
repository visibility.
