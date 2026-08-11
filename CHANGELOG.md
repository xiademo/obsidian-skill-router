# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-08-11

### Added

- Canonical routing Registry schema `0.2` with field provenance and source hashes.
- Deterministic hybrid retrieval using exact aliases, BM25F-style field scoring,
  character n-grams, package soft priors, and reciprocal-rank fusion.
- Route policy, compatibility, negative-trigger, and action-risk gates.
- Five-state decisions and deterministic multi-Skill planning.
- Auditable typed graph candidates and optional graph shadow reranking.
- Approved offline enrichment proposals with protected governance fields.
- Managed-file ownership, staged generation, strict validation, and rollback.
- Thin route-only Hook adapter and expanded evaluation metrics.

### Changed

- Only exact `SKILL.md` files discover Skills by default.
- Schema `0.1` remains readable for one migration cycle; scans now write `0.2`.

[Unreleased]: https://github.com/xiademo/obsidian-skill-router/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/xiademo/obsidian-skill-router/releases/tag/v0.2.0
