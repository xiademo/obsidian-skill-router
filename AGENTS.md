# obsidian-skill-router Project Constitution

## Product Boundary

`obsidian-skill-router` is a local-first, package-first Obsidian Skill Router.

It scans already-downloaded local AI agent skills and generates an Obsidian routing vault so agents can choose skills through:

Global Package Map -> Package Index -> Skill Card -> Graph Relations / Risk Information

The project may index, organize, route, recommend, and lightly govern local skills.

## Out Of Scope

Do not add behavior that installs, updates, deletes, downloads, publishes, or executes skills.

Treat every user-provided Skill root, including `~/.codex/skills`, as read-only.

Do not read or activate `.agents/skills`.

Do not add MCP, Obsidian plugin APIs, UI, services, servers, cloud sync, external API calls, or vector databases.

## Test Artifact Boundary

Generated vaults, real scan outputs, eval reports, caches, and temporary outputs belong under ignored `test_artifacts/`.

Do not commit generated vault files.

Unit tests must use synthetic or desensitized fixtures only.

Real `.codex/skills` content may be used only for smoke scans and observation, and scan output must remain under `test_artifacts/`.
