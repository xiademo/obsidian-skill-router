# Configuration and Governance

[Documentation home](README.md) · [CLI reference](CLI.md) ·
[Architecture](ARCHITECTURE.md)

Configuration changes discovery and deterministic inference. Routing overlays
record governed facts for named Skills. Enrichment files import offline
proposals under a narrower permission set.

Configuration and routing overlays fail closed on unknown fields, invalid
types, unsafe paths, or ambiguous selectors. Enrichment is a non-authoritative
import: only well-formed, approved proposals with a current source hash can
participate in routing; every other proposal is inert.

## Configuration file

Schema `0.2` uses JSON:

```json
{
  "schema_version": "0.2",
  "skills_roots": ["C:/Users/you/.codex/skills"],
  "vault": "C:/Users/you/Documents/SkillRouterVault",
  "discovery": {
    "max_depth": 6,
    "legacy_readme_skills": false
  },
  "routing": {
    "package_rules": {
      "coding-workflow": ["repository", "code", "test", "build"]
    }
  },
  "graph": {
    "max_workflow_next_per_source": 3
  },
  "policy": {
    "risk_keywords": {
      "high": ["delete", "publish", "payment", "send secret", "execute"]
    }
  }
}
```

| Field | Contract |
| --- | --- |
| `schema_version` | Optional; when present must be `0.2` |
| `skills_roots` | Non-empty list of local directory strings |
| `vault` | Non-empty target directory string |
| `discovery.max_depth` | Integer from `0` through `32`; default `6` |
| `discovery.legacy_readme_skills` | Boolean; default `false` |
| `routing.package_rules` | Package name to non-empty string list |
| `graph.max_workflow_next_per_source` | Integer from `0` through `20`; default `3` |
| `policy.risk_keywords` | Risk level to non-empty string list |

CLI `--skills-root` and `--vault` values override their configuration values.
Nested `discovery`, `routing`, `graph`, and `policy` sections are supported only
in JSON.

Legacy top-level `package_rules` and `risk_keywords`, plus simple YAML, remain
readable for the `0.2` migration cycle and emit warnings. New nested settings
must not use YAML.

Example: [`examples/config.example.json`](../examples/config.example.json).

## Routing overlays

Pass one or more overlays to `scan`:

```bash
obsidian-skill-router scan \
  --skills-root "/path/to/.codex/skills" \
  --vault "/path/to/SkillRouterVault" \
  --routing-rules ./routing-rules.json
```

Overlay shape:

```json
{
  "schema_version": "0.2",
  "skills": [
    {
      "root_id": "skills",
      "relative_path": "example-router",
      "package": "safety-governance",
      "aliases": ["example-router"],
      "positive_triggers": ["audit local routing rules"],
      "negative_triggers": ["execute the selected Skill"],
      "route_mode": "default",
      "risk_level": "low",
      "permissions": [],
      "permissions_known": true,
      "network_access": false,
      "writes_files": false
    }
  ]
}
```

Each entry must use exactly one selector:

- `id` or `skill_id`;
- `root_id` together with `relative_path`;
- `name` or `declared_name`, only when the name uniquely identifies one Skill.

Unmatched, duplicate, or ambiguous selectors stop the scan. Prefer stable ID or
root-relative path over name for long-lived governance.

Common governed fields:

| Group | Fields |
| --- | --- |
| Taxonomy | `package`, `alternate_packages`, `aliases` |
| Activation | `positive_triggers`, `negative_triggers`, `route_mode`, `trigger_mode`, `default_routable`, `requires_explicit_user_intent` |
| Risk | `risk_level`, `topic_risk`, `action_risk`, `actions`, `side_effects`, `permissions`, `permissions_known`, `network_access`, `writes_files` |
| Contracts | `artifacts`, `requires`, `conflicts`, `workflow_next` |
| Review candidates | `related_candidates`, `workflow_candidates`, `duplicate_candidates`, `conflict_candidates` |

Enums:

- `route_mode`: `default`, `manual`, `explicit-only`, `profile-only`.
- `trigger_mode`: `default`, `direct-or-semantic-match`,
  `strong-semantic-match`, `explicit-only`, `profile-only`, `manual`.
- `risk_level` and `action_risk`: `low`, `medium`, `high`, `unknown`.
- `topic_risk`: `low-topic`, `high-topic`, `unknown`.

Booleans must be JSON booleans, not strings. List fields must contain strings.
Structured actions recognized by the controlled destructive-action rules can
raise a declared lower risk; their effective risk cannot be forced back to
`low` by an overlay.

Example:
[`examples/routing-rules.example.json`](../examples/routing-rules.example.json).

## Offline enrichment proposals

The product never calls an LLM. Another offline process may write proposal JSON
for review:

```json
{
  "proposals": [
    {
      "skill_id": "example-router-deadbeef",
      "field": "aliases",
      "value": ["routing-auditor"],
      "source_hash": "current-source-sha256",
      "source_span": "SKILL.md:1-12",
      "confidence": 0.9,
      "status": "approved"
    }
  ]
}
```

Required proposal fields: target Skill, `field`, `value`, `source_hash`,
non-empty `source_span`, finite `confidence` from `0` through `1`, and `status`.

Only `status: approved` proposals with a current source hash can affect the
Registry. Pending, rejected, invalid, and stale proposals remain audit
candidates.

Allowed enrichment fields:

- `aliases`;
- `positive_triggers` and `negative_triggers`;
- `actions` (also accepted through the input alias `capabilities`);
- related, workflow, duplicate, and conflict candidate relations.

Enrichment cannot directly change source identity, Package governance, route
mode, permissions, compatibility, explicit risk, or confirmed dependencies.
Candidate relations never become confirmed solely because an enrichment
proposal was approved.

Example:
[`examples/enrichment-proposals.example.json`](../examples/enrichment-proposals.example.json).

## Precedence and provenance

Normalized fields follow this precedence:

1. `SKILL.md` frontmatter for identity and declared metadata.
2. Explicit routing overlay for governed routing and risk facts.
3. Manifest extension fields.
4. Explicit Markdown sections.
5. Deterministic inference.

Every inferred or overridden field records provenance in the canonical
Registry. Approved enrichment records confidence and source span. Review the
generated audit files before allowing a changed Registry into automation.

## Safe operating rules

- Keep the Vault outside all Skill roots.
- Do not point the scanner at `.agents/skills`.
- Keep local paths, Skill names, descriptions, hashes, and workload audits out
  of public commits unless reviewed and redacted.
- Run `validate --strict --check-sources` after every governance change.
- Evaluate `hybrid` on a held-out and redacted workload before Hook integration.
