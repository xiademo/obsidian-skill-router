# Eval and Quality Checks

Read this file before changing routing, classification, graph generation, report generation, or eval behavior.

## Eval Fixtures

- `tests/fixtures/eval_skills` and `tests/fixtures/eval_cases.json` are the synthetic baseline for expected routing behavior.
- `tests/fixtures/eval_hard_skills` and `tests/fixtures/eval_hard_cases.json` are adversarial fixtures for ambiguity, safety, and false-trigger pressure.
- `tests/fixtures/eval_cn_ambiguity_cases.json` covers Chinese ambiguous-routing scenarios when that fixture is wired into a task.
- Keep fixtures synthetic or desensitized. Do not copy real `.codex/skills` bodies into tests.

## Metrics

Use eval output to watch at least:

- Package Hit@1
- Skill Hit@1
- Skill Hit@3
- False Trigger Rate

Treat these metrics as routing quality signals, not security guarantees.

## Agent-First Evaluation

Use the progressive route evaluator when changing the compact routing index or
the `route` command:

```powershell
$env:PYTHONPATH = "src"
python -m obsidian_skill_router eval --agent-first --vault .\test_artifacts\fixture_vault --cases .\tests\fixtures\eval_agent_first_redteam_cases.json --report .\test_artifacts\agent-first-eval-report.md
```

The report measures primary-skill precision, candidate recall, correct
continue/no-skill decisions, high-risk review gating, constraints, and the
estimated routing payload cost. It does not execute skills or claim final task
completion. Keep any manually reviewed real-task audit file under ignored
`test_artifacts/` and redact task text and local skill metadata as needed.

## Regression Workflow

Run the baseline eval:

```powershell
$env:PYTHONPATH = "src"
python -m obsidian_skill_router eval --skills-root .\tests\fixtures\eval_skills --cases .\tests\fixtures\eval_cases.json
```

Run the adversarial eval:

```powershell
$env:PYTHONPATH = "src"
python -m obsidian_skill_router eval --skills-root .\tests\fixtures\eval_hard_skills --cases .\tests\fixtures\eval_hard_cases.json
```

Write a Markdown report only when the task needs a durable artifact:

```powershell
$env:PYTHONPATH = "src"
python -m obsidian_skill_router eval --skills-root .\tests\fixtures\eval_skills --cases .\tests\fixtures\eval_cases.json --report .\test_artifacts\eval-report.md
```

## Acceptance Criteria

- Existing passing baseline cases should remain passing unless the task explicitly changes routing expectations.
- Any new failure in adversarial evals should be explained before completion.
- Risk labels, duplicate/conflict candidates, and graph relations are lightweight routing metadata and should not be presented as formal security findings.
