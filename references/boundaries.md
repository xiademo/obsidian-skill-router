# Project Boundaries

Read this file before implementation, repository-structure changes, or any task that touches generated vaults or source skill roots.

## Product Boundary

`obsidian-skill-router` is a local-first, package-first Obsidian Skill Router. It scans already-downloaded local AI agent skills and generates an Obsidian routing vault so agents can choose skills through:

Global Package Map -> Package Index -> Skill Card -> Graph Relations / Risk Information

The project may index, organize, route, recommend, and lightly govern local skills.

## Out Of Scope

- Do not add behavior that installs, updates, deletes, downloads, publishes, syncs, or executes skills.
- Treat every user-provided Skill root, including `~/.codex/skills`, as read-only.
- Do not read or activate `.agents/skills`.
- Do not add MCP, Obsidian plugin APIs, UI, services, servers, cloud sync, external API calls, or vector databases.

## Test Artifact Boundary

- Keep generated vaults, real scan outputs, eval reports, caches, and temporary outputs under ignored `test_artifacts/`.
- Do not commit generated vault files.
- Use synthetic or desensitized fixtures for unit tests.
- Use real `.codex/skills` content only for smoke scans and observation, with scan output under `test_artifacts/`.

## Skill Metadata Boundary

- Treat root `SKILL.md`, `agents/`, and `references/` as companion Skill metadata for agents using this repository.
- Do not turn README into the agent execution guide; README remains project/user documentation.
- Do not add empty resource directories. Add `scripts/` or `assets/` only when a concrete reusable script or output asset exists.
