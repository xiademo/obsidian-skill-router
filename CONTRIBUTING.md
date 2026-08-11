# Contributing

Contributions are welcome through issues and pull requests.

## Development setup

```powershell
git clone https://github.com/xiademo/obsidian-skill-router.git
cd obsidian-skill-router
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m unittest discover -s tests
```

On macOS or Linux, activate with `source .venv/bin/activate`.

## Change requirements

- Keep the runtime dependency-free unless a design review justifies otherwise.
- Do not add Skill installation, download, update, publication, or execution.
- Do not scan or activate `.agents/skills`.
- Use only synthetic or desensitized test fixtures.
- Keep generated Vaults and real local scans under ignored `test_artifacts/`.
- Add regression tests for behavior changes.
- Preserve the CLI JSON envelope and document schema migrations.

Before opening a pull request, run:

```powershell
python -m unittest discover -s tests
python -m compileall -q src tests
python -m build
python -m twine check dist/*
```

Pull requests should explain the problem, behavior change, validation evidence,
and any compatibility or security impact.
