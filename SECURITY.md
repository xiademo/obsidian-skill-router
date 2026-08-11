# Security Policy

## Supported versions

Security fixes target the latest released minor version.

| Version | Supported |
|---|---|
| 0.2.x | Yes |
| 0.1.x | Migration support only |

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use the repository's
[private vulnerability reporting form](https://github.com/xiademo/obsidian-skill-router/security/advisories/new).

Include affected version, operating system, reproduction steps, impact, and any
suggested mitigation. Remove API keys, tokens, private Skill bodies, personal
paths, and generated Vault content before submitting evidence.

The maintainer will acknowledge a complete report when practicable, validate
the finding, and coordinate disclosure after a fix is available.

## Security boundary

This project indexes local metadata and never installs, downloads, publishes,
or executes Skills. Risk labels are routing metadata, not a sandbox or formal
security guarantee. See [Project boundaries](references/boundaries.md).

Generated Vaults are sensitive metadata inventories. They contain local source
root paths, Skill names and descriptions, timestamps, and content hashes even
though source Skill bodies are not copied. Keep generated Vaults local by
default. Before cloud sync, publication, or sharing, remove personal paths and
review every generated artifact for private capability or environment details.
