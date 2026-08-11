# Obsidian Skill Router

[![CI](https://github.com/xiademo/obsidian-skill-router/actions/workflows/ci.yml/badge.svg)](https://github.com/xiademo/obsidian-skill-router/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

[简体中文](README.zh-CN.md) | English

`obsidian-skill-router` is a local-first CLI that indexes already-downloaded AI
agent skills into an Obsidian-compatible routing vault.

It does not install, update, delete, download, publish, execute, sync, or run
skills. It reads local files and writes Markdown/CSV router artifacts to a vault
directory.

> [!WARNING]
> A generated Vault is a sensitive metadata inventory: it contains local source
> paths, Skill names/descriptions, timestamps, and hashes. Keep it local unless
> you have reviewed and redacted it. Source Skill bodies are not copied.

## Installation

Python 3.10 or newer is required. The router has zero runtime dependencies.

Recommended isolated installation:

```bash
pipx install git+https://github.com/xiademo/obsidian-skill-router.git@v0.2.0
obsidian-skill-router --version
```

Alternatively:

```bash
uv tool install git+https://github.com/xiademo/obsidian-skill-router.git@v0.2.0
```

See [Installation](docs/INSTALLATION.md) for virtual-environment, development,
upgrade, and uninstall instructions.

## Commands

```powershell
obsidian-skill-router scan --skills-root C:\path\to\skills --vault C:\path\to\vault
obsidian-skill-router scan --skills-dir C:\path\to\skills --vault C:\path\to\vault
obsidian-skill-router scan --skills-root C:\path\to\skills --vault C:\path\to\vault --dry-run
obsidian-skill-router scan --skills-root C:\path\to\skills --vault C:\path\to\vault --routing-rules .\routing-rules.json --enrichment .\enrichment-proposals.json
obsidian-skill-router validate --vault C:\path\to\vault --strict --check-sources
obsidian-skill-router report --vault C:\path\to\vault
obsidian-skill-router route --vault C:\path\to\vault --task "extract text from this PDF" --strategy hybrid
obsidian-skill-router eval --skills-root C:\path\to\skills --cases .\tests\fixtures\eval_cases.json --strategy legacy --strategy hybrid
Get-Content .\hook-event.json | obsidian-skill-router --json hook --vault C:\path\to\vault --strategy hybrid
```

During development, run without installation:

```powershell
$env:PYTHONPATH = "src"
python -m obsidian_skill_router scan --skills-root .\tests\fixtures\eval_skills --vault .\test_artifacts\fixture_vault
```

## JSON Policy

Use `--json` before the command for stable machine-readable output.

Success shape:

```json
{"ok": true, "command": "scan", "data": {"skills": 3}}
```

Error shape:

```json
{"ok": false, "command": "scan", "error": {"type": "ValueError", "message": "..."}}
```

Errors never include credentials because this tool has no auth path and does not
handle tokens.

## Generated Vault

The generated vault contains:

```text
00_Router/
01_Packages/
02_Workflows/
03_Graph/
04_Inbox/
05_Reports/
```

Skill cards and package indexes use Obsidian wikilinks. Graph edges are written
as CSV files for external inspection.

`00_Router/routing-index.json` schema `0.2` is the canonical machine registry.
Obsidian Markdown and CSV files are auditable derived views. Schema `0.1` stays
readable for one migration cycle; strict validation rejects it.

Use `route` for agent-first selection: it returns `recommend`,
`continue_without_skill`, or `review_required`, plus short evidence and a Skill
Card path. `decision.type` adds `clarify_required` and `skill_plan`. The hybrid
strategy combines exact/alias matches, field-weighted sparse retrieval,
character n-grams, package soft priors, reciprocal-rank fusion, policy gates,
and optional graph shadow reranking. It never executes a skill.

The implementation form is a Python routing core plus an optional thin hook
adapter. The Python core owns discovery, registry compilation, retrieval,
policy, planning, validation, and evaluation. The `hook` command only converts
one host event into a route decision; it does not scan, load, or execute Skill
bodies. See [Architecture](docs/ARCHITECTURE.md).

Reports include scan, risk, quality, calibration, and duplicate/conflict
candidates. Risk labels are lightweight routing metadata, not security
guarantees.

Graph relations use this typed set:

- `related`
- `workflow_next`
- `complements`
- `fallback_to`
- `duplicates`
- `conflicts_with`
- `governs`
- `requires`

Only confirmed edges are written to compatibility `skill_edges.csv` and may
affect planning. Candidate edges remain in `skill_edges.audit.csv` and
`04_Inbox/candidates/` until reviewed.

## Repository Boundaries

This repository keeps the router implementation separate from generated test
output:

- `src/obsidian_skill_router/` is the application/library source.
- `tests/` contains automated unit tests and disposable fixtures created at
  test runtime.
- `README.md` and `pyproject.toml` are project metadata.
- Root `SKILL.md`, `agents/`, and `references/` are companion Skill metadata
  for agents using this repository; they do not expand the CLI product
  boundary or add runtime services.
- `test_artifacts/` is for manual or real-config scan output, including vaults
  generated from a local Codex skill root. These files are not skill source and
  are ignored by Git.

When testing with your real Codex skill configuration, write the generated vault
under `test_artifacts/`:

```powershell
python -m obsidian_skill_router scan --skills-root C:\Users\me\.codex\skills --vault .\test_artifacts\real_vault
```

## Local Config

`--config` accepts the fail-closed schema 0.2 JSON format below. A small YAML
subset remains available for the legacy top-level `package_rules` and
`risk_keywords` format and emits a migration warning. Unknown fields and invalid
types are rejected so a misspelled policy cannot be silently ignored. CLI flags
override config values.

Example JSON:

```json
{
  "schema_version": "0.2",
  "skills_roots": ["C:/Users/me/.codex/skills"],
  "vault": "C:/Users/me/Vaults/SkillRouter",
  "discovery": {
    "max_depth": 6,
    "legacy_readme_skills": false
  },
  "routing": {
    "package_rules": {
      "coding-workflow": ["git", "test", "build"]
    }
  },
  "graph": {
    "max_workflow_next_per_source": 3
  },
  "policy": {
    "risk_keywords": {
      "high": ["delete", "deploy", "credential"]
    }
  }
}
```

## Development

Run tests without installing the package:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests
```

If the package is installed in editable mode, `PYTHONPATH` is not required.

Run the synthetic routing eval:

```powershell
$env:PYTHONPATH = "src"
python -m obsidian_skill_router eval --skills-root .\tests\fixtures\eval_skills --cases .\tests\fixtures\eval_cases.json
```

Run the agent-first red-team evaluation against a generated vault:

```powershell
$env:PYTHONPATH = "src"
python -m obsidian_skill_router eval --agent-first --vault .\test_artifacts\fixture_vault --cases .\tests\fixtures\eval_agent_first_redteam_cases.json --report .\test_artifacts\agent-first-eval-report.md
```

An optional `--audit` file may contain a locally ignored, redacted manual audit
set. It is intentionally not a committed fixture because it can contain real
user task wording and local skill IDs.

```json
{
  "cases": [
    {
      "id": "local-001",
      "user_task": "<redacted historical task>",
      "expected_decision": "recommend",
      "expected_primary_package": "coding-workflow",
      "acceptable_primary_skills": ["<vault skill id>"],
      "expected_related_packages": [],
      "importance": 2
    }
  ]
}
```

## Project governance

- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [Code of Conduct](CODE_OF_CONDUCT.md)
- [Changelog](CHANGELOG.md)
- [Test and release plan](docs/TEST_PLAN.md)
- [Maintainer release procedure](docs/RELEASING.md)

## License

Released under the [MIT License](LICENSE).
