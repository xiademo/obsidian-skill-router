# Examples

[Documentation home](../docs/README.md) ·
[Configuration reference](../docs/CONFIGURATION.md) ·
[CLI reference](../docs/CLI.md)

These files demonstrate input contracts. They contain no real Skill bodies,
local source inventory, or user workload data.

| File | Purpose |
| --- | --- |
| [`config.example.json`](config.example.json) | Schema `0.2` discovery, routing, graph, and policy configuration |
| [`routing-rules.example.json`](routing-rules.example.json) | Explicit per-Skill governance overlay |
| [`enrichment-proposals.example.json`](enrichment-proposals.example.json) | Offline enrichment proposal awaiting review |
| [`eval-cases.example.json`](eval-cases.example.json) | Minimal positive and negative routing cases |

## Use the examples safely

1. Copy the needed JSON into an ignored local path under `test_artifacts/`.
2. Replace every example path, Skill selector, and source hash.
3. Run a dry scan first.
4. Generate into a dedicated test Vault.
5. Validate source hashes before routing.

```powershell
obsidian-skill-router scan `
  --config .\test_artifacts\config.local.json `
  --routing-rules .\test_artifacts\routing-rules.local.json `
  --enrichment .\test_artifacts\enrichment.local.json `
  --dry-run
```

The committed enrichment example uses `status: pending` and a non-current
example source hash, so it cannot affect routing. The routing overlay selector
must match exactly one discovered Skill or the scan fails closed.

## Evaluation example

The committed cases are illustrative. Use them only with a synthetic Skill
root that contains the expected Skill, or edit expectations to match your
fixture.

```powershell
obsidian-skill-router eval `
  --skills-root .\tests\fixtures\eval_skills `
  --cases .\tests\fixtures\eval_cases.json `
  --strategy legacy `
  --strategy hybrid
```

Real workload audits may contain sensitive task wording and local Skill IDs.
Keep them ignored and redacted; never add them to `examples/`.
