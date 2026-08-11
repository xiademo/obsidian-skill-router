# CLI Reference

[Documentation home](README.md) · [Installation](INSTALLATION.md) ·
[Configuration](CONFIGURATION.md)

## Command form

```text
obsidian-skill-router [--json] [--version] <command> [options]
```

Global options must appear before the subcommand:

| Option | Meaning |
| --- | --- |
| `--json` | Emit exactly one stable JSON envelope on stdout, including errors |
| `--version` | Print the installed package version |

Available commands: `scan`, `validate`, `report`, `route`, `hook`, and `eval`.

## Safe first workflow

```bash
obsidian-skill-router scan --skills-root "/path/to/.codex/skills" --vault "/path/to/SkillRouterVault"
obsidian-skill-router validate --vault "/path/to/SkillRouterVault" --strict --check-sources
obsidian-skill-router --json route --vault "/path/to/SkillRouterVault" --strategy hybrid --task "audit Skill routing"
```

The Vault must not be the Skill root or a directory inside it.

## `scan`

Discover Skill metadata, compile `routing-index.json`, then generate the
Obsidian audit views.

```text
obsidian-skill-router scan [--skills-root PATH] [--vault PATH]
                           [--config PATH] [--dry-run]
                           [--routing-rules PATH ...]
                           [--enrichment PATH ...]
                           [--legacy-readme-skills]
```

| Option | Meaning |
| --- | --- |
| `--skills-root`, `--skills-dir` | Local Skill root; repeat for multiple roots |
| `--vault` | Target generated Vault |
| `--config` | Schema `0.2` JSON or legacy simple YAML configuration |
| `--dry-run` | Discover and classify without writing Vault files |
| `--routing-rules` | Governance overlay JSON; repeatable |
| `--enrichment` | Offline proposal JSON; repeatable |
| `--legacy-readme-skills` | Explicitly enable deprecated README-only discovery |

At least one Skill root is required. `--vault` is optional only with
`--dry-run`. The scanner refuses `.agents/skills`, unsafe path overlap, and
ambiguous governance selectors.

## `validate`

Validate Registry schema, managed-file hashes, card paths, wikilinks, graph
edges, and optional source drift.

```text
obsidian-skill-router validate --vault PATH [--strict] [--check-sources]
```

| Option | Meaning |
| --- | --- |
| `--vault` | Generated Vault to validate |
| `--strict` | Reject legacy schema and non-canonical data |
| `--check-sources` | Recompute source hashes and detect Registry drift |

Use both checks before automation or release evidence:

```bash
obsidian-skill-router validate --vault "/path/to/SkillRouterVault" --strict --check-sources
```

## `route`

Return a route decision; never invoke a Skill.

```text
obsidian-skill-router route --vault PATH --task TEXT
                             [--strategy legacy|hybrid|hybrid-graph]
                             [--profile NAME] [--explain]
```

| Option | Meaning |
| --- | --- |
| `--vault` | Valid generated Vault |
| `--task` | Task text to route |
| `--strategy` | Ranking strategy; default is `legacy` in version `0.2.0` |
| `--profile` | Optional compatibility and policy context |
| `--explain` | Include up to two alternative candidates |

Strategies:

- `legacy`: migration baseline retained for one version cycle.
- `hybrid`: recommended explicit strategy; exact/alias, BM25F, character
  n-gram, Package soft prior, RRF, and policy gates.
- `hybrid-graph`: experimental shadow strategy; bounded confirmed-edge lift.

`decision.type` is one of `recommend`, `clarify_required`,
`continue_without_skill`, `review_required`, or `skill_plan`.

## `hook`

Read one JSON object from stdin and wrap the same Python router. The adapter
never scans or executes Skill bodies.

```text
obsidian-skill-router hook --vault PATH
                            [--strategy legacy|hybrid|hybrid-graph]
                            [--profile NAME]
```

The event must contain one of `task`, `prompt`, or `input`. Optional event
fields are `profile` and boolean `explain`.

```json
{
  "task": "audit Skill routing",
  "profile": "windows",
  "explain": true
}
```

```powershell
Get-Content .\hook-event.json |
  obsidian-skill-router --json hook --vault .\SkillRouterVault --strategy hybrid
```

The response declares `"action": "route_only"` and
`"executes_skill": false`.

## `report`

Read all managed Markdown reports from `05_Reports/`.

```text
obsidian-skill-router report --vault PATH
```

Human mode prints report text. JSON mode returns the reports keyed by managed
filename.

## `eval`

Run repeatable routing cases against Skill roots or a generated Vault.

```text
obsidian-skill-router eval [--skills-root PATH ...] [--vault PATH] --cases PATH
                            [--config PATH] [--report PATH]
                            [--agent-first] [--audit PATH]
                            [--strategy STRATEGY ...]
                            [--gate-config PATH]
```

| Option | Meaning |
| --- | --- |
| `--skills-root`, `--skills-dir` | Scan one or more synthetic or redacted roots |
| `--vault` | Evaluate an existing canonical Registry; when supplied, it takes precedence over Skill roots |
| `--cases` | Required JSON case file |
| `--config` | Optional scan configuration |
| `--report` | Write one Markdown report; requires exactly one strategy |
| `--agent-first` | Measure five-state decisions and candidate payload cost |
| `--audit` | Optional ignored, redacted real-workload audit file |
| `--strategy` | Repeat to compare strategies in one run |
| `--gate-config` | Fail with nonzero exit when a release threshold is missed |

Never commit real task wording, local Skill IDs, or generated Vault output.
Use `test_artifacts/` for ignored local evaluation evidence.

## Exit and JSON behavior

Successful commands return exit code `0`; argument, policy, validation, and gate
failures return nonzero. With global `--json`, stdout contains one JSON object:

```json
{"ok": true, "command": "validate", "data": {"valid": true, "errors": []}}
```

```json
{"ok": false, "command": "validate", "error": {"type": "ValueError", "message": "..."}}
```
