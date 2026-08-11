---
name: obsidian-skill-router
description: Build, audit, validate, report on, and evaluate local-first Obsidian routing vaults generated from already-downloaded Codex or AI agent skills. Use for obsidian-skill-router CLI tasks, generated vault inspection, global package maps, package indexes, skill cards, graph relations, risk metadata, quality reports, duplicate or conflict candidates, and routing metrics such as Package Hit@1, Skill Hit@1, Skill Hit@3, and False Trigger Rate. Do not use for installing, updating, deleting, downloading, publishing, syncing, executing, or modifying source skills; do not read or activate .agents/skills.
---

# Obsidian Skill Router

Use this Skill to work on the local CLI that turns existing skill metadata into an Obsidian-compatible routing vault.

## Task Intake

- Identify the requested action: scan, validate, report, eval, inspect a generated vault, or change router code/docs.
- For an agent task, use `route` only when the work is multi-step, tool or artifact oriented, cross-domain, or uncertain. Do not pay a router cost for ordinary conversation.
- Confirm the skill root inputs, target vault, optional config, and optional eval cases before running commands.
- Read `references/boundaries.md` before any implementation or repository-structure change.
- Read `references/workflow.md` for CLI command order, JSON mode, dry-run usage, and PowerShell examples.
- Read `references/eval-quality.md` before changing routing, classification, graph, report, or eval behavior.

## Standard Workflow

- Use `scan --dry-run` first when checking a new skill root or config.
- Write generated vaults only to the requested vault path; for real local skill roots, prefer ignored paths under `test_artifacts/`.
- Run `validate` after generating a vault and before reporting success.
- Run synthetic and adversarial evals after changing routing behavior or evaluation code.
- For a generated vault, call `route --vault <vault> --task <task>` before opening package indexes or Skill Cards. Respect `continue_without_skill`; treat `review_required` as an approval gate, not permission to execute.
- Keep user-facing docs separate from Skill instructions: README is project documentation, while this file is the agent entry point.

## Safety Rules

- Treat source skill roots as read-only.
- Do not install, update, delete, download, publish, sync, execute, or modify skills.
- Do not copy real local skill bodies into fixtures, README, reports, or this Skill.
- Do not add MCP servers, Obsidian plugin APIs, UI, services, cloud sync, external API calls, or vector databases.
- Keep generated vaults, real scan outputs, eval reports, caches, and temporary outputs out of committed source unless they are synthetic fixtures.
