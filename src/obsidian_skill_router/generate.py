from __future__ import annotations

import csv
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from typing import Iterable

from .graph import build_package_edges, build_skill_edges_with_pruning
from .models import PACKAGES, GraphEdge, SkillRecord, vault_dirs
from .reports import conflict_candidates, graph_quality, high_risk_audit, quality_report, risk_reasons, risk_report, scan_report
from .routing import routing_index_payload
from .sanitize import markdown_plain


PACKAGE_GUIDANCE: dict[str, tuple[str, str]] = {
    "document-processing": ("Documents, media, and format conversion", "PDF, DOCX, spreadsheets, slides, OCR, transcription, markdown conversion"),
    "career-resume": ("Career and resume workflows", "Resumes, CVs, cover letters, interviews, job-search material"),
    "coding-workflow": ("Software engineering work", "Repos, code, tests, debugging, reviews, frontend, CLI, implementation"),
    "research-writing": ("Research and structured writing", "Literature, citations, reports, notes, meeting docs, specs, PRDs"),
    "automation-ops": ("Automation, cloud, and operations", "Deployments, cron, CI, monitoring, infrastructure, Cloudflare/Vercel-style ops"),
    "memory-knowledge": ("Knowledge-base and memory workflows", "Obsidian, notes, vaults, indexes, durable knowledge capture"),
    "safety-governance": ("Risk, policy, and approval guidance", "Security, secrets, threat modeling, high-risk tool governance"),
}
PACKAGE_NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
SKILL_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
MANAGED_ROOTS = {
    ".obsidian-skill-router", "00_Router", "01_Packages", "02_Workflows",
    "03_Graph", "04_Inbox", "05_Reports",
}


def generate_vault(
    vault: Path,
    records: list[SkillRecord],
    roots: list[str],
    warnings: list[str],
    candidates: list[dict[str, object]] | None = None,
    *,
    max_workflow_next_per_source: int = 3,
) -> dict[str, int]:
    """Build a complete vault in staging, validate it, then publish managed files."""
    vault = vault.resolve()
    for root_value in roots:
        source_root = Path(root_value).resolve()
        if vault == source_root or vault.is_relative_to(source_root) or source_root.is_relative_to(vault):
            raise ValueError(f"Vault and Skill source root must not overlap: {vault} / {source_root}")
    _validate_generated_components(records)
    vault.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".obsidian-skill-router-staging-", dir=vault.parent) as temporary:
        staging = Path(temporary)
        result = _generate_vault_contents(
            staging,
            records,
            roots,
            warnings,
            candidates or [],
            max_workflow_next_per_source,
        )
        _write_managed_manifest(staging)
        from .validate import validate_vault

        ok, errors = validate_vault(staging, strict=True)
        if not ok:
            raise ValueError("staged vault validation failed: " + "; ".join(errors))
        _publish_managed(staging, vault)
        return result


def _generate_vault_contents(
    vault: Path,
    records: list[SkillRecord],
    roots: list[str],
    warnings: list[str],
    candidates: list[dict[str, object]],
    max_workflow_next_per_source: int,
) -> dict[str, int]:
    for directory in vault_dirs(vault):
        directory.mkdir(parents=True, exist_ok=True)

    skill_edges, workflow_pruning = build_skill_edges_with_pruning(
        records, max_workflow_next_per_source=max_workflow_next_per_source
    )
    package_edges = build_package_edges(records)
    _write_router(vault, records, skill_edges, package_edges)
    _write_packages(vault, records, skill_edges)
    _write_inbox(vault, records, candidates)
    _write_graph(vault, skill_edges, package_edges)
    _write_reports(vault, records, roots, warnings, skill_edges, package_edges, workflow_pruning)
    return {"skills": len(records), "skill_edges": len(skill_edges), "package_edges": len(package_edges)}


def dry_run_summary(records: list[SkillRecord]) -> dict[str, int]:
    return {
        "skills": len(records),
        "classified": sum(1 for record in records if record.package),
        "unclassified": sum(1 for record in records if not record.package),
        "needs_review": len(quality_report(records).needs_review),
    }


def _write_router(vault: Path, records: list[SkillRecord], skill_edges: list[GraphEdge], package_edges: list[GraphEdge]) -> None:
    packages = _packages(records)
    package_counts = {package: sum(1 for record in records if record.package == package) for package in packages}
    rows = ["| Package | Purpose | Use for | Skills | Index |", "|---|---|---|---:|---|"]
    for package in packages:
        purpose, use_for = PACKAGE_GUIDANCE.get(package, ("Governed local skill package", "Tasks explicitly assigned to this package by routing rules"))
        rows.append(f"| {package} | {purpose} | {use_for} | {package_counts[package]} | [[01_Packages/{package}/Package Index|Package Index]] |")
    rows.append(f"| needs-review | Unclassified or incomplete metadata | Review missing package, metadata, or risk signals | {sum(1 for record in records if not record.package)} | [[04_Inbox/needs-review]] |")
    _write(
        vault / "00_Router" / "Skill Package Map.md",
        "# Skill Package Map\n\n"
        "Use this as the global routing entry point. Pick the package whose purpose and use-for text best match the task, then open that package index to compare candidate skills.\n\n"
        + "\n".join(rows)
        + "\n",
    )

    search_rows = ["| Skill | Package | Description | When to use | Risk | Confidence |", "|---|---|---|---|---|---:|"]
    for record in records:
        link = _skill_link(record)
        search_rows.append(
            f"| {link} | {record.package or 'unclassified'} | {_cell(record.description)} | "
            f"{_cell(record.when_to_use)} | {record.risk_level} | {record.confidence:.2f} |"
        )
    _write(vault / "00_Router" / "Global Skill Search Index.md", "# Global Skill Search Index\n\n" + "\n".join(search_rows) + "\n")

    graph_rows = ["| Source | Target | Relation | Reason | Confidence |", "|---|---|---|---|---:|"]
    for edge in [*skill_edges, *package_edges]:
        graph_rows.append(f"| {edge.source} | {edge.target} | {edge.relation} | {_cell(edge.reason)} | {edge.confidence:.2f} |")
    _write(vault / "00_Router" / "Cross-Package Graph.md", "# Cross-Package Graph\n\n" + "\n".join(graph_rows) + "\n")
    index = routing_index_payload(records, skill_edges)
    (vault / "00_Router" / "routing-index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_packages(vault: Path, records: list[SkillRecord], edges: list[GraphEdge]) -> None:
    edge_notes = _edge_notes(edges, records)
    by_package: dict[str, list[SkillRecord]] = defaultdict(list)
    for record in records:
        if record.package:
            by_package[record.package].append(record)

    for package in _packages(records):
        skills = sorted(by_package.get(package, []), key=lambda item: item.name.lower())
        purpose, use_for = PACKAGE_GUIDANCE.get(package, ("Governed local skill package", "Tasks explicitly assigned by routing rules"))
        rows = ["| Skill | Best for | Inputs | Outputs | Risk | Confidence | Related / Next / Warnings |", "|---|---|---|---|---|---:|---|"]
        for skill in skills:
            notes = _package_notes(edge_notes.get(skill.id, {}))
            rows.append(
                f"| {_skill_link(skill)} | {_cell(skill.when_to_use or skill.description)} | "
                f"{_cell(', '.join(skill.input_types))} | {_cell(', '.join(skill.output_types))} | "
                f"{skill.risk_level} | {skill.confidence:.2f} | {_cell(notes)} |"
            )
        _write(
            vault / "01_Packages" / package / "Package Index.md",
            f"# {package} Package Index\n\n"
            f"- Purpose: {purpose}\n"
            f"- Use for: {use_for}\n\n"
            "Compare skills by best-fit task, expected inputs/outputs, risk, confidence, and nearby graph notes.\n\n"
            + "\n".join(rows)
            + "\n",
        )
        for skill in skills:
            _write(vault / "01_Packages" / package / "skills" / f"{skill.id}.md", _skill_card(skill, edge_notes.get(skill.id, {})))


def _write_inbox(vault: Path, records: list[SkillRecord], candidates: list[dict[str, object]]) -> None:
    review_records = [record for record in records if _needs_review(record)]
    _write(
        vault / "04_Inbox" / "needs-review.md",
        "# Needs Review\n\n" + _record_list(review_records) + "\n",
    )
    for record in records:
        if not record.package:
            _write(vault / "04_Inbox" / "unclassified" / f"{record.id}.md", _skill_card(record, {}))
        if _needs_review(record):
            _write(vault / "04_Inbox" / "needs-review" / f"{record.id}.md", _skill_card(record, {}))
    if candidates:
        _write(
            vault / "04_Inbox" / "candidates" / "enrichment-proposals.json",
            json.dumps(candidates, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )


def _write_graph(vault: Path, skill_edges: list[GraphEdge], package_edges: list[GraphEdge]) -> None:
    confirmed = [edge for edge in skill_edges if edge.review_state == "confirmed"]
    _write_csv(vault / "03_Graph" / "skill_edges.csv", ["source", "target", "relation", "reason", "confidence"], confirmed)
    _write_csv(
        vault / "03_Graph" / "skill_edges.audit.csv",
        ["source", "target", "relation", "reason", "confidence", "origin", "review_state", "evidence", "rule_version"],
        skill_edges,
    )
    _write_csv(vault / "03_Graph" / "package_edges.csv", ["source_package", "target_package", "relation", "reason"], package_edges)


def _write_reports(
    vault: Path,
    records: list[SkillRecord],
    roots: list[str],
    warnings: list[str],
    skill_edges: list[GraphEdge],
    package_edges: list[GraphEdge],
    workflow_pruning: object | None = None,
) -> None:
    scan = scan_report(records, roots, warnings)
    risk = risk_report(records)
    risk_audit = high_risk_audit(records)
    quality = quality_report(records)
    conflicts = conflict_candidates(records)
    graph = graph_quality(records, skill_edges, package_edges, workflow_pruning)
    package_counts = {package: sum(1 for record in records if record.package == package) for package in PACKAGES}
    unclassified = [record for record in records if not record.package]
    missing_descriptions = [record for record in records if not record.description]
    missing_when = [record for record in records if not record.when_to_use]
    _write(
        vault / "05_Reports" / "scan-report.md",
        "# Scan Report\n\n"
        f"- Scanned roots: {_plain_list(scan.scanned_roots)}\n"
        f"- Skills found: {scan.skills_found}\n"
        f"- Classified: {scan.classified}\n"
        f"- Unclassified: {scan.unclassified}\n"
        f"- Missing metadata / needs review: {scan.missing_metadata}\n"
        f"- Warnings: {len(scan.warnings)}\n",
    )
    _write(
        vault / "05_Reports" / "risk-report.md",
        "# Risk Report\n\n"
        f"## High Risk\n\n{_record_list(risk.high_risk)}\n\n"
        "## High Risk Audit\n\n"
        f"### Clearly High Risk\n\n{_risk_audit_list(risk_audit['clearly_high_risk'])}\n\n"
        f"### Likely Medium / False Positive\n\n{_risk_audit_list(risk_audit['likely_medium_or_false_positive'])}\n\n"
        f"### Needs Manual Review\n\n{_risk_audit_list(risk_audit['needs_manual_review'])}\n\n"
        f"## Unknown Risk\n\n{_record_list(risk.unknown_risk)}\n",
    )
    _write(
        vault / "05_Reports" / "quality-report.md",
        "# Quality Report\n\n"
        f"- Duplicate names: {len(quality.duplicate_names)}\n"
        f"- Duplicate paths: {len(quality.duplicate_paths)}\n"
        f"- Low confidence: {len(quality.low_confidence)}\n"
        f"- Needs review: {len(quality.needs_review)}\n\n"
        "## Needs Review\n\n"
        f"{_record_list(quality.needs_review)}\n",
    )
    _write(
        vault / "05_Reports" / "calibration-report.md",
        "# Calibration Report\n\n"
        "Risk labels are lightweight routing metadata, not security guarantees.\n\n"
        "## Summary\n\n"
        f"- Scanned candidate skills: {scan.skills_found}\n"
        f"- Generated skill cards: {len(records)}\n"
        f"- Unclassified skills: {scan.unclassified}\n"
        f"- Missing descriptions: {len(missing_descriptions)}\n"
        f"- Missing when_to_use: {len(missing_when)}\n"
        f"- Unknown risk level: {len(risk.unknown_risk)}\n"
        f"- High-risk skills: {len(risk.high_risk)}\n\n"
        "## Package Distribution\n\n"
        + "\n".join(f"- {package}: {package_counts[package]}" for package in PACKAGES)
        + f"\n- unclassified: {len(unclassified)}\n\n"
        "## Unclassified Skills\n\n"
        f"{_record_list(unclassified)}\n\n"
        "## Missing Descriptions\n\n"
        f"{_record_list(missing_descriptions[:50])}\n\n"
        "## Missing when_to_use\n\n"
        f"{_record_list(missing_when[:50])}\n\n"
        "## Unknown Risk Level\n\n"
        f"{_record_list(risk.unknown_risk[:80])}\n\n"
        "## High Risk Skills\n\n"
        f"{_risk_list_with_reasons(risk.high_risk[:80])}\n\n"
        "## Possible Duplicate Skills\n\n"
        f"{_duplicate_names(quality.duplicate_names)}\n\n"
        "## Possible Conflict Skills\n\n"
        f"{_conflict_list(conflicts[:50])}\n\n"
        "## Examples of Good Classification\n\n"
        f"{_record_list(_good_examples(records))}\n\n"
        "## Examples of Bad Classification / Review Needed\n\n"
        f"{_record_list(_bad_examples(records))}\n\n"
        "## Recommended Rule Changes\n\n"
        "- Keep Cloudflare product references mostly in automation-ops unless security-specific terms such as WAF, DDoS, Turnstile, or secrets dominate.\n"
        "- Keep UI/frontend/design implementation skills in coding-workflow rather than career-resume or generic automation buckets.\n"
        "- Treat explicit delete, publish, deployment, payment, credential transmission, and command execution capabilities as high action-risk signals.\n"
        "- Keep topic mentions such as token, secret, deployment, or compression separate from executable action risk.\n"
        "- Treat explicit network access, browser automation, external writes, and filesystem mutation as medium action-risk signals.\n"
        "- Keep any proposed new package, such as design-frontend, as a future taxonomy discussion rather than adding it in this loop.\n",
    )
    _write(
        vault / "05_Reports" / "duplicate-conflict-report.md",
        "# Duplicate and Conflict Candidate Report\n\n"
        "These are lightweight heuristics only; they are review prompts, not semantic matches.\n\n"
        "## Duplicate Names\n\n"
        f"{_duplicate_names(quality.duplicate_names)}\n\n"
        "## Conflict Candidates\n\n"
        f"{_conflict_list(conflicts)}\n",
    )
    _write(
        vault / "05_Reports" / "graph-quality-report.md",
        "# Graph Quality Report\n\n"
        "This report surfaces graph edge quality issues; it is not a pass/fail proof of routing correctness.\n\n"
        "## Relation Counts Before Pruning\n\n"
        f"{_mapping_list(graph['relation_counts_before_pruning'])}\n\n"
        "## Relation Counts After Pruning\n\n"
        f"{_mapping_list(graph['relation_counts'])}\n\n"
        "## Workflow Pruning Summary\n\n"
        f"{_workflow_pruning_report(graph['workflow_pruning'])}\n\n"
        "## Invalid Relation Names\n\n"
        f"{_edge_list(graph['invalid_relations'])}\n\n"
        "## Missing Reasons\n\n"
        f"{_edge_list(graph['missing_reasons'])}\n\n"
        "## Isolated Skills\n\n"
        f"{_record_list(graph['isolated_skills'][:80])}\n\n"
        "## Workflow Density\n\n"
        f"- workflow_next edges: {graph['workflow_next_edges']}\n"
        f"- density warning threshold: {graph['workflow_density_limit']}\n"
        f"- high-density workflow_next warning: {graph['workflow_density_warning']}\n\n"
        "## Top High-Degree Skills\n\n"
        f"{_high_degree_list(graph['top_high_degree_skills'])}\n\n"
        "## Related Edge Noise\n\n"
        f"- related edge count: {graph['related_edge_count']}\n"
        f"- related density threshold: {graph['related_density_limit']}\n"
        f"- related density warning: {graph['related_density_warning']}\n\n"
        "## Top High-Degree Related Nodes\n\n"
        f"{_high_degree_list(graph['top_high_degree_related_nodes'])}\n\n"
        "## Downgraded Edge Examples\n\n"
        f"{_edge_list(graph['workflow_pruning']['downgraded_to_related_examples'])}\n\n"
        "## Removed Edge Examples\n\n"
        f"{_edge_list(graph['workflow_pruning']['removed_workflow_edge_examples'])}\n\n"
        "## Downgraded Related Examples\n\n"
        f"{_edge_list(graph['downgraded_related_examples'])}\n\n"
        "## Suspicious Related Edges Without Strong Reason\n\n"
        f"{_edge_list(graph['suspicious_related_edges'])}\n\n"
        "## Conflict Edge Examples\n\n"
        f"{_edge_list(graph['conflict_edge_examples'])}\n\n"
        "## Duplicate Edge Examples\n\n"
        f"{_edge_list(graph['duplicate_edge_examples'])}\n\n"
        "## Duplicates And Conflicts Summary\n\n"
        f"- duplicate edges: {graph['duplicate_edges']}\n"
        f"- conflict edges: {graph['conflict_edges']}\n",
    )


def _skill_card(record: SkillRecord, edge_notes: dict[str, list[str]]) -> str:
    warnings = "\n".join(f"- {_plain(warning)}" for warning in record.warnings) or "- None"
    return (
        "---\n"
        "type: agent_skill\n"
        'schema_version: "0.2"\n'
        f'id: "{_yaml(record.id)}"\n'
        f'name: "{_yaml(record.name)}"\n'
        f'package: "{_yaml(record.package or "unclassified")}"\n'
        f'description: "{_yaml(record.description)}"\n'
        f"when_to_use: {_yaml_list([record.when_to_use] if record.when_to_use else [])}\n"
        f"input_types: {_yaml_list(record.input_types)}\n"
        f"output_types: {_yaml_list(record.output_types)}\n"
        f"permissions: {_yaml_list(record.permissions)}\n"
        f"permissions_known: {str(record.permissions_known).lower()}\n"
        f'risk_level: "{_yaml(record.risk_level)}"\n'
        f'source_root_id: "{_yaml(record.source_root_id)}"\n'
        f'source_relative_path: "{_yaml(record.source_relative_path)}"\n'
        f'source_hash: "{_yaml(record.source_hash)}"\n'
        f'status: "{_yaml(record.status)}"\n'
        "tags: []\n"
        f'last_scanned: "{_yaml(record.last_scanned)}"\n'
        "---\n\n"
        f"# {_plain(record.name)}\n\n"
        f"- Skill ID: `{_code_text(record.id)}`\n"
        f"- Package: {_plain(record.package or 'unclassified')}\n"
        f"- Source: `{_code_text(f'{record.source_root_id}:{record.source_relative_path}')}`\n"
        f"- Source SHA-256: `{_code_text(record.source_hash)}`\n"
        f"- Detected files: {_plain_list(record.detected_files)}\n"
        f"- Risk level: {record.risk_level}\n"
        f"- Confidence: {record.confidence:.2f}\n\n"
        "## Routing Notes\n\n"
        f"- Description: {_plain(record.description or 'Missing')}\n"
        f"- When to use: {_plain(record.when_to_use or 'Missing')}\n"
        f"- When not to use: {_plain(_when_not_to_use(record))}\n"
        f"- Input types: {_plain_list(record.input_types, fallback='Missing')}\n"
        f"- Output types: {_plain_list(record.output_types, fallback='Missing')}\n"
        f"- Alternate packages: {_plain_list(record.alternate_packages)}\n"
        f"- Route mode: {record.route_mode}\n"
        f"- Aliases: {_plain_list(record.aliases)}\n"
        f"- Positive triggers: {_plain_list(record.positive_triggers)}\n"
        f"- Negative triggers: {_plain_list(record.negative_triggers)}\n\n"
        "## Risk Notes\n\n"
        f"- Risk level: {record.risk_level}\n"
        f"- Review warnings: {len(record.warnings)}\n\n"
        "## Related Skills\n\n"
        f"{_bullet_links(edge_notes.get('related', []))}\n\n"
        "## Workflow Next\n\n"
        f"{_bullet_links(edge_notes.get('workflow_next', []))}\n\n"
        "## Complements\n\n"
        f"{_bullet_links(edge_notes.get('complements', []))}\n\n"
        "## Fallbacks And Alternatives\n\n"
        f"{_bullet_links(edge_notes.get('fallback_to', []))}\n\n"
        "## Duplicates And Conflicts\n\n"
        f"{_bullet_links([*edge_notes.get('duplicates', []), *edge_notes.get('conflicts_with', [])])}\n\n"
        "## Governance\n\n"
        f"{_bullet_links(edge_notes.get('governs', []))}\n\n"
        "## Review Warnings\n\n"
        f"{warnings}\n"
    )


def _edge_notes(edges: list[GraphEdge], records: list[SkillRecord]) -> dict[str, dict[str, list[str]]]:
    notes: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    by_id = {record.id: record for record in records}
    for edge in edges:
        source_is_skill = edge.source in by_id
        target_is_skill = edge.target in by_id
        target_label = _node_label(edge.target, by_id)
        source_label = _node_label(edge.source, by_id)
        if source_is_skill:
            notes[edge.source][edge.relation].append(f"{target_label} - {_plain(edge.reason)}")
        if target_is_skill and edge.relation in {"related", "complements", "duplicates", "conflicts_with", "governs"}:
            notes[edge.target][edge.relation].append(f"{source_label} - {_plain(edge.reason)}")
        if target_is_skill and edge.relation == "workflow_next":
            notes[edge.target][edge.relation].append(f"Previous: {source_label} - {_plain(edge.reason)}")
    return notes


def _node_label(node: str, by_id: dict[str, SkillRecord]) -> str:
    if node in by_id:
        return _skill_link(by_id[node])
    return node


def _package_notes(notes: dict[str, list[str]]) -> str:
    parts = []
    for relation in ["related", "workflow_next", "complements", "fallback_to", "duplicates", "conflicts_with", "governs"]:
        values = notes.get(relation, [])
        if values:
            parts.append(f"{relation}: {len(values)}")
    return ", ".join(parts) or "None"


def _when_not_to_use(record: SkillRecord) -> str:
    if not record.package:
        return "Do not auto-call until package classification is reviewed."
    if record.risk_level == "high":
        return "Do not call for low-risk or read-only work without checking safer alternatives and approvals."
    if record.risk_level == "unknown":
        return "Do not auto-call until risk is reviewed."
    return "Do not call when the task does not match the listed inputs, outputs, or package purpose."


def _write_csv(path: Path, headers: list[str], edges: Iterable[GraphEdge]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        for edge in edges:
            if headers[0] == "source_package":
                writer.writerow([_csv_text(value) for value in (edge.source, edge.target, edge.relation, edge.reason)])
            elif len(headers) == 5:
                writer.writerow([
                    _csv_text(value)
                    for value in (edge.source, edge.target, edge.relation, edge.reason, f"{edge.confidence:.2f}")
                ])
            else:
                writer.writerow([_csv_text(value) for value in (
                    edge.source, edge.target, edge.relation, edge.reason, f"{edge.confidence:.2f}", edge.origin,
                    edge.review_state, edge.evidence, edge.rule_version,
                )])


def _record_list(records: list[SkillRecord]) -> str:
    return "\n".join(f"- {_skill_link(record)} ({record.risk_level}, {record.confidence:.2f})" for record in records) or "- None"


def _risk_list_with_reasons(records: list[SkillRecord]) -> str:
    return "\n".join(
        f"- {_skill_link(record)} ({record.risk_level}, {record.confidence:.2f}) - {_plain_list(risk_reasons(record), separator='; ')}"
        for record in records
    ) or "- None"


def _risk_audit_list(records: list[tuple[SkillRecord, list[str]]]) -> str:
    return "\n".join(
        f"- {_skill_link(record)} - {_plain_list(reasons, separator='; ')}"
        for record, reasons in records
    ) or "- None"


def _duplicate_names(duplicates: dict[str, list[str]]) -> str:
    if not duplicates:
        return "- None"
    return "\n".join(f"- {_plain(name)}: {_plain_list(paths)}" for name, paths in sorted(duplicates.items()))


def _conflict_list(conflicts: list[tuple[SkillRecord, SkillRecord, str]]) -> str:
    if not conflicts:
        return "- None"
    return "\n".join(f"- {_skill_link(left)} <> {_skill_link(right)}: {_plain(reason)}" for left, right, reason in conflicts)


def _mapping_list(values: object) -> str:
    if not isinstance(values, dict) or not values:
        return "- None"
    return "\n".join(f"- {_plain(str(key))}: {_plain(str(value))}" for key, value in values.items())


def _edge_list(edges: object) -> str:
    if not isinstance(edges, list) or not edges:
        return "- None"
    return "\n".join(
        f"- {_plain(edge.source)} -> {_plain(edge.target)} ({_plain(edge.relation)}): {_plain(edge.reason or 'Missing')}"
        for edge in edges[:80]
    )


def _workflow_pruning_report(value: object) -> str:
    if not isinstance(value, dict):
        return "- None"
    return "\n".join(
        [
            f"- workflow_next before: {value.get('before_count', 0)}",
            f"- workflow_next after: {value.get('after_count', 0)}",
            f"- downgraded_to_related count: {value.get('downgraded_to_related_count', 0)}",
            f"- removed_workflow_edges count: {value.get('removed_workflow_edges_count', 0)}",
        ]
    )


def _high_degree_list(value: object) -> str:
    if not isinstance(value, list) or not value:
        return "- None"
    return "\n".join(
        f"- {_skill_link(record)}: outgoing={outgoing}, incoming={incoming}, workflow_outgoing={workflow_outgoing}"
        for record, outgoing, incoming, workflow_outgoing in value
    )


def _good_examples(records: list[SkillRecord]) -> list[SkillRecord]:
    return [record for record in records if record.package and record.confidence >= 0.5 and record.risk_level != "unknown"][:10]


def _bad_examples(records: list[SkillRecord]) -> list[SkillRecord]:
    return [record for record in records if not record.package or record.confidence < 0.35 or record.risk_level == "unknown"][:10]


def _bullet_links(values: list[str]) -> str:
    return "\n".join(f"- {value}" for value in values) or "- None"


def _skill_link(record: SkillRecord) -> str:
    label = _wikilink_label(record.name)
    if record.package:
        return f"[[01_Packages/{record.package}/skills/{record.id}|{label}]]"
    return f"[[04_Inbox/unclassified/{record.id}|{label}]]"


def _cell(value: str) -> str:
    return _plain(value)


def _plain(value: str) -> str:
    return markdown_plain(value)


def _plain_list(values: Iterable[str], *, fallback: str = "None", separator: str = ", ") -> str:
    rendered = separator.join(_plain(value) for value in values)
    return rendered or fallback


def _code_text(value: str) -> str:
    return " ".join(str(value or "").split()).replace("`", "\\`")


def _wikilink_label(value: str) -> str:
    return (
        " ".join(
            "".join(character for character in str(value or "") if ord(character) >= 32 and not 127 <= ord(character) <= 159).split()
        )
        .replace("&", "&amp;")
        .replace("|", "&#124;")
        .replace("[", "&#91;")
        .replace("]", "&#93;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _csv_text(value: object) -> str:
    text = str(value)
    stripped = text.lstrip()
    if stripped.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


def _needs_review(record: SkillRecord) -> bool:
    return (
        not record.package
        or record.confidence < 0.25
        or not record.description
        or not record.when_to_use
        or not record.input_types
        or not record.output_types
        or record.risk_level == "unknown"
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_managed_manifest(vault: Path) -> None:
    manifest_path = vault / ".obsidian-skill-router" / "managed-files.json"
    files = []
    for path in sorted(item for item in vault.rglob("*") if item.is_file() and item != manifest_path):
        files.append({"path": path.relative_to(vault).as_posix(), "sha256": _file_hash(path)})
    _write(
        manifest_path,
        json.dumps({"schema_version": "0.2", "files": files}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _manifest_entries(vault: Path) -> dict[str, str]:
    path = vault / ".obsidian-skill-router" / "managed-files.json"
    if _is_linklike(path):
        raise ValueError(f"Managed manifest cannot be a symlink: {path}")
    if not path.exists():
        return {}
    if not path.is_file():
        raise ValueError(f"Managed manifest is not a file: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read managed manifest {path}: {exc}") from exc
    entries: dict[str, str] = {}
    for item in payload.get("files", []):
        if not isinstance(item, dict) or not item.get("path") or not item.get("sha256"):
            raise ValueError(f"Malformed managed manifest entry: {path}")
        relative = _managed_relative(str(item["path"])).as_posix()
        digest = str(item["sha256"]).lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"Malformed managed file hash for {relative}")
        if relative in entries:
            raise ValueError(f"Duplicate managed path: {relative}")
        entries[relative] = digest
    return entries


def _publish_managed(staging: Path, vault: Path) -> None:
    old_entries = _manifest_entries(vault)
    new_entries = _manifest_entries(staging)
    old_paths = set(old_entries)
    new_paths = set(new_entries)
    manifest_relative = ".obsidian-skill-router/managed-files.json"
    backup_root = staging / ".publish-backup"
    published: list[tuple[str, Path]] = []
    backed_up: list[tuple[Path, Path]] = []
    stale_parents: set[Path] = set()
    try:
        for relative in sorted(new_paths | {manifest_relative}):
            source = _safe_managed_join(staging, relative)
            target = _safe_managed_join(vault, relative)
            if _is_linklike(source) or not source.is_file():
                raise ValueError(f"Generated managed source is not a regular file: {source}")
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if _is_linklike(target) or not target.is_file():
                    raise ValueError(f"Managed target is not a regular file: {target}")
                if relative not in old_paths and relative != manifest_relative:
                    raise ValueError(f"Refusing to overwrite user-owned Vault file: {target}")
                if relative in old_entries and _file_hash(target) != old_entries[relative]:
                    raise ValueError(f"Refusing to overwrite modified managed file: {target}")
                backup = _safe_managed_join(backup_root, relative)
                backup.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target, backup)
                backed_up.append((backup, target))
            os.replace(source, target)
            published.append((relative, target))
        for relative in sorted(old_paths - new_paths, reverse=True):
            target = _safe_managed_join(vault, relative)
            if target.exists():
                if _is_linklike(target) or not target.is_file():
                    raise ValueError(f"Managed stale target is not a regular file: {target}")
                if _file_hash(target) != old_entries[relative]:
                    raise ValueError(f"Refusing to delete modified managed file: {target}")
                backup = _safe_managed_join(backup_root, relative)
                backup.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target, backup)
                backed_up.append((backup, target))
                stale_parents.add(target.parent)
    except Exception:
        for _, target in reversed(published):
            if target.is_file():
                target.unlink()
        for backup, target in reversed(backed_up):
            if backup.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(backup, target)
        raise
    _remove_empty_managed_parents(vault, stale_parents)
    for directory in vault_dirs(vault):
        directory.mkdir(parents=True, exist_ok=True)


def _validate_generated_components(records: list[SkillRecord]) -> None:
    for record in records:
        if not SKILL_ID_RE.fullmatch(record.id):
            raise ValueError(f"Unsafe generated Skill id: {record.id!r}")
        if record.package and (len(record.package) > 64 or not PACKAGE_NAME_RE.fullmatch(record.package)):
            raise ValueError(f"Unsafe generated package name: {record.package!r}")


def _managed_relative(value: str) -> Path:
    normalized = value.replace("\\", "/")
    if not normalized or normalized.startswith(("/", "//")) or re.match(r"^[A-Za-z]:", normalized):
        raise ValueError(f"Unsafe managed path: {value!r}")
    pure = PurePosixPath(normalized)
    if any(part in {"", ".", ".."} for part in pure.parts) or not pure.parts or pure.parts[0] not in MANAGED_ROOTS:
        raise ValueError(f"Unsafe managed path: {value!r}")
    return Path(*pure.parts)


def _safe_managed_join(base: Path, value: str) -> Path:
    relative = _managed_relative(value)
    root = base.resolve()
    current = root
    for part in relative.parts:
        current = current / part
        if _is_linklike(current):
            raise ValueError(f"Managed path traverses a symlink: {current}")
    resolved = current.resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise ValueError(f"Managed path escapes root: {value!r}")
    return current


def _remove_empty_managed_parents(vault: Path, directories: set[Path]) -> None:
    root = vault.resolve()
    for directory in sorted(directories, key=lambda value: len(value.parts), reverse=True):
        current = directory
        while current != root and current.is_relative_to(root):
            if _is_linklike(current):
                break
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent


def _is_linklike(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _yaml(value: str) -> str:
    rendered = json.dumps(str(value or ""), ensure_ascii=False)[1:-1]
    for marker, escape in (("[", "\\u005b"), ("]", "\\u005d"), ("<", "\\u003c"), (">", "\\u003e"), ("!", "\\u0021")):
        rendered = rendered.replace(marker, escape)
    return rendered


def _yaml_list(values: list[str]) -> str:
    if not values:
        return "[]"
    return "[" + ", ".join(f'"{_yaml(value)}"' for value in values) + "]"


def _packages(records: list[SkillRecord]) -> list[str]:
    extras = sorted({record.package for record in records if record.package and record.package not in PACKAGES})
    return [*PACKAGES, *extras]
