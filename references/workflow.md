# CLI Workflow

Use these workflows for local development, validation, and manual smoke checks.

## Command Order

1. Inspect inputs with `scan --dry-run` when the skill root or config is new.
2. Generate the vault with `scan`.
3. Validate the generated vault with `validate`.
4. Inspect reports with `report`.
5. Run evals when routing behavior, classification, graph generation, reports, or eval code changes.

## PowerShell Examples

Run without installation from the repository root:

```powershell
$env:PYTHONPATH = "src"
python -m obsidian_skill_router scan --skills-root .\tests\fixtures\eval_skills --vault .\test_artifacts\fixture_vault --dry-run
python -m obsidian_skill_router scan --skills-root .\tests\fixtures\eval_skills --vault .\test_artifacts\fixture_vault
python -m obsidian_skill_router validate --vault .\test_artifacts\fixture_vault
python -m obsidian_skill_router report --vault .\test_artifacts\fixture_vault
```

Use a real Codex skill root only for smoke scans and keep output ignored:

```powershell
$env:PYTHONPATH = "src"
python -m obsidian_skill_router scan --skills-root "$HOME\.codex\skills" --vault .\test_artifacts\real_vault
python -m obsidian_skill_router validate --vault .\test_artifacts\real_vault
```

## JSON Mode

Put `--json` before the command for machine-readable output:

```powershell
$env:PYTHONPATH = "src"
python -m obsidian_skill_router --json scan --skills-root .\tests\fixtures\eval_skills --vault .\test_artifacts\fixture_vault --dry-run
```

Success output uses:

```json
{"ok": true, "command": "scan", "data": {"skills": 3}}
```

Error output uses:

```json
{"ok": false, "command": "scan", "error": {"type": "ValueError", "message": "..."}}
```

## Config Notes

- `--skills-root` and `--skills-dir` are aliases and may be repeated.
- CLI flags override config file values.
- `--config` accepts JSON or the small YAML subset supported by the project.
- Keep generated output under `test_artifacts/` during development unless the user explicitly provides another vault path.
