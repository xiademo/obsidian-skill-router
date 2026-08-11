# Installation

Obsidian Skill Router requires Python 3.10 or newer and has no runtime
dependencies outside the Python standard library.

## Recommended: isolated CLI installation

Install directly from the public GitHub repository with `pipx`:

```bash
pipx install git+https://github.com/xiademo/obsidian-skill-router.git@v0.2.0
obsidian-skill-router --version
```

Or use `uv`:

```bash
uv tool install git+https://github.com/xiademo/obsidian-skill-router.git@v0.2.0
obsidian-skill-router --version
```

## Install into a virtual environment

Windows PowerShell:

```powershell
git clone https://github.com/xiademo/obsidian-skill-router.git
cd obsidian-skill-router
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install .
obsidian-skill-router --version
```

macOS or Linux:

```bash
git clone https://github.com/xiademo/obsidian-skill-router.git
cd obsidian-skill-router
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
obsidian-skill-router --version
```

## Development installation

```bash
python -m pip install -e ".[dev]"
python -m unittest discover -s tests
```

## First run

The router reads existing Skill metadata and writes a generated Vault. It does
not modify or execute source Skills.

Use a dedicated local Vault. Generated output includes source paths, Skill
metadata, timestamps, and hashes; review and redact it before any sharing or
cloud synchronization.

```powershell
obsidian-skill-router scan `
  --skills-root "$HOME\.codex\skills" `
  --vault "$HOME\Documents\SkillRouterVault"

obsidian-skill-router validate `
  --vault "$HOME\Documents\SkillRouterVault" `
  --strict `
  --check-sources

obsidian-skill-router route `
  --vault "$HOME\Documents\SkillRouterVault" `
  --strategy hybrid `
  --task "audit and improve Skill routing"
```

Use only `.codex/skills` or another explicitly provided local Skill root. The
project intentionally does not scan `.agents/skills`.

## Upgrade

```bash
pipx upgrade obsidian-skill-router
```

For a Git URL installation that cannot be upgraded in place, reinstall:

```bash
pipx install --force git+https://github.com/xiademo/obsidian-skill-router.git@v0.2.0
```

## Uninstall

```bash
pipx uninstall obsidian-skill-router
```

Virtual-environment installations can be removed with:

```bash
python -m pip uninstall obsidian-skill-router
```
