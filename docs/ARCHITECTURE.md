# Architecture Decision: Python Router Core, Thin Hook Adapter

## Decision

The product uses a deterministic Python router as the authoritative runtime.
A host hook is an optional transport adapter around that router, not a second
routing implementation.

This split prevents host-specific hook lifecycle, stdout, timeout, and context
rules from becoming part of ranking semantics. It also keeps `scan`, `route`,
`validate`, `eval`, and tests on one code path. The hook accepts one JSON event,
returns one route envelope, and never executes a Skill.

## Product Boundary

The repository contains registry compiler, router, reports, synthetic fixtures,
and test helpers. It contains no executable Skill bodies. Real local Skills may
be read during ignored smoke tests, but are never copied, modified, installed,
or executed.

## Compiled-Knowledge Model

The architecture applies the LLM Wiki principle of compiling reusable knowledge
once instead of reconstructing it at every query:

1. Raw `SKILL.md`, manifest, routing overlay, and enrichment proposals remain
   source material.
2. `routing-index.json` is the normalized, source-linked machine registry.
3. Obsidian cards, indexes, graphs, and reports are derived views.
4. Every inferred field and graph edge records provenance, evidence, confidence,
   review state, and rule version.
5. Approved enrichment can add routing metadata. Pending, rejected, or stale
   proposals stay auditable and cannot affect routing.

This is compilation, not online RAG and not runtime LLM classification.

## Retrieval And Policy Pipeline

The Python router performs:

1. Unicode NFKC normalization and exact name/alias detection.
2. Field-weighted sparse retrieval over aliases, activation conditions,
   description, capabilities, artifacts, I/O, package, and provider.
3. Character bigram/trigram retrieval for Chinese, mixed-language, typo, and
   compact-token recall.
4. Top-package soft priors and weighted reciprocal-rank fusion.
5. Hard policy gates for manual, explicit-only, profile-only, compatibility,
   negative triggers, and unsupported actions.
6. Five-state decisions: recommend, clarify, no-skill, review, or skill plan.

Risk is not relevance. Topic mentions do not raise execution risk. Only explicit
actions, permissions, side effects, or routing overlays can raise action risk.

## Multi-Skill Planning

Explicit sequence words split a task into subproblems. Each subproblem receives
up to five candidates. For at most eight subproblems, a bitmask dynamic program
selects a minimum-cost Skill cover; larger plans use greedy cover. Confirmed
`requires` and `workflow_next` edges determine topological order. Cycles,
unsupported steps, ambiguity, or high-risk steps elevate the whole plan to
clarification or review. Planning never invokes Skills.

## Graph Use

The web-graph lesson is separation of hard predicates from authority/proximity
signals. Policy gates run before graph scoring. Only the lexical top 20 and
confirmed one-hop neighbors enter three bounded Personalized PageRank rounds.
Graph lift is capped at 8%. Candidate relations never affect default routing.
The graph strategy remains shadow-only until held-out and real-workload
ablation show positive Top-3 gain without increased over-expansion.

## Operational Safety

Vault generation happens in staging, validates before publication, records
managed file hashes, and removes only files owned by the previous managed
manifest. User-created Vault files remain untouched. A failed staged build
leaves the previous published Vault intact.
