# Documentation

Obsidian Skill Router is a deterministic Python router and Registry compiler
for already-downloaded local Skills. Start with the user task you need to
complete; implementation details live behind the task guides.

## Start here

| Goal | Read |
| --- | --- |
| Install the pinned CLI and create the first Vault | [Installation](INSTALLATION.md) |
| Find every command and option | [CLI reference](CLI.md) |
| Configure discovery, routing rules, and enrichment | [Configuration](CONFIGURATION.md) |
| Understand Registry, retrieval, policy, graph, and planning | [Architecture](ARCHITECTURE.md) |
| Reproduce routing metrics and release gates | [Test plan](TEST_PLAN.md) |
| Cut and verify a release | [Maintainer release procedure](RELEASING.md) |

## Recommended workflow

1. Install a tagged release into an isolated environment.
2. Choose a dedicated local Vault outside every Skill source root.
3. Run `scan`, then `validate --strict --check-sources`.
4. Route with explicit `--strategy hybrid` during the `0.2` migration cycle.
5. Add routing overlays only for facts you can govern and review.
6. Integrate the thin `hook` adapter only after direct `route` decisions pass
   your own workload evaluation.

## Examples

The [`examples/`](../examples/README.md) directory contains small, reviewable
JSON contracts for configuration, routing overlays, enrichment proposals, and
evaluation cases. They contain no real Skill bodies or user workload data.

## Product boundary

The project reads local Skill metadata and writes a generated Registry plus
Obsidian-compatible audit views. It does not install, download, modify,
publish, or execute Skills. It does not scan `.agents/skills`, call a runtime
LLM, or add a service, MCP server, vector database, or Obsidian plugin.

[Back to project README](../README.md)
