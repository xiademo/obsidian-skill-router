from __future__ import annotations

from collections import defaultdict
import re

from .classify import assess_risk_level
from .config import DEFAULT_RISK_KEYWORDS
from .models import GRAPH_RELATIONS, GraphEdge, QualityReport, RiskReport, ScanReport, SkillRecord


def scan_report(records: list[SkillRecord], roots: list[str], warnings: list[str]) -> ScanReport:
    return ScanReport(
        scanned_roots=roots,
        skills_found=len(records),
        classified=sum(1 for record in records if record.package),
        unclassified=sum(1 for record in records if not record.package),
        missing_metadata=sum(1 for record in records if _needs_review(record)),
        warnings=warnings,
    )


def risk_report(records: list[SkillRecord]) -> RiskReport:
    return RiskReport(
        high_risk=[record for record in records if record.risk_level == "high"],
        unknown_risk=[record for record in records if record.risk_level == "unknown"],
    )


def quality_report(records: list[SkillRecord]) -> QualityReport:
    names: dict[str, list[str]] = defaultdict(list)
    paths: dict[str, int] = defaultdict(int)
    for record in records:
        names[record.name.lower()].append(record.source_path)
        paths[record.source_path] += 1
    return QualityReport(
        duplicate_names={name: paths for name, paths in names.items() if len(paths) > 1},
        duplicate_paths=sorted(path for path, count in paths.items() if count > 1),
        low_confidence=[record for record in records if record.confidence < 0.25],
        needs_review=[record for record in records if _needs_review(record)],
    )


def conflict_candidates(records: list[SkillRecord], limit: int = 80) -> list[tuple[SkillRecord, SkillRecord, str]]:
    candidates: list[tuple[SkillRecord, SkillRecord, str]] = []
    ordered = sorted(records, key=lambda record: (record.package, record.name.lower(), record.id))
    for index, left in enumerate(ordered):
        for right in ordered[index + 1:]:
            if left.package != right.package:
                continue
            reasons = _conflict_reasons(left, right)
            if reasons:
                candidates.append((left, right, ", ".join(reasons)))
            if len(candidates) >= limit:
                return candidates
    return candidates


def risk_reasons(record: SkillRecord) -> list[str]:
    text = " ".join(
        [
            record.name,
            record.description,
            record.when_to_use,
            " ".join(record.input_types),
            " ".join(record.output_types),
        ]
    ).lower()
    action_text = " ".join([*record.actions, *record.side_effects, *record.permissions]).lower()
    recomputed, reasons = assess_risk_level("unknown", text, DEFAULT_RISK_KEYWORDS, record.package, action_text=action_text)
    if record.risk_level == "high" and recomputed != "high":
        return ["classified high; no high-risk keyword reason found, manual review needed"]
    return reasons


def high_risk_audit(records: list[SkillRecord]) -> dict[str, list[tuple[SkillRecord, list[str]]]]:
    buckets: dict[str, list[tuple[SkillRecord, list[str]]]] = {
        "clearly_high_risk": [],
        "likely_medium_or_false_positive": [],
        "needs_manual_review": [],
    }
    for record in records:
        if record.risk_level != "high":
            continue
        reasons = risk_reasons(record)
        joined = " ".join(reasons).lower()
        if any(signal in joined for signal in ["credential", "secret", "token", "api key", "deploy", "production", "private key", "payment", "delete", "rm -rf"]):
            buckets["clearly_high_risk"].append((record, reasons))
        elif "explicit risk metadata" in joined:
            buckets["needs_manual_review"].append((record, reasons))
        else:
            buckets["likely_medium_or_false_positive"].append((record, reasons))
    return buckets


def graph_quality(
    records: list[SkillRecord],
    skill_edges: list[GraphEdge],
    package_edges: list[GraphEdge],
    workflow_pruning: object | None = None,
) -> dict[str, object]:
    all_edges = [*skill_edges, *package_edges]
    relation_counts: dict[str, int] = defaultdict(int)
    invalid_relations: list[GraphEdge] = []
    missing_reasons: list[GraphEdge] = []
    connected: set[str] = set()
    for edge in all_edges:
        relation_counts[edge.relation] += 1
        if edge.relation not in GRAPH_RELATIONS:
            invalid_relations.append(edge)
        if not edge.reason.strip():
            missing_reasons.append(edge)
        connected.add(edge.source)
        connected.add(edge.target)
    skill_ids = {record.id for record in records}
    isolated = sorted((record for record in records if record.id not in connected), key=lambda record: (record.name.lower(), record.id))
    workflow_edges = relation_counts.get("workflow_next", 0)
    density_limit = max(50, len(records) * 10)
    duplicate_edges = relation_counts.get("duplicates", 0)
    conflict_edges = relation_counts.get("conflicts_with", 0)
    top_high_degree = _top_high_degree(records, all_edges)
    related_edges = [edge for edge in skill_edges if edge.relation == "related"]
    conflict_edge_list = [edge for edge in skill_edges if edge.relation == "conflicts_with"]
    duplicate_edge_list = [edge for edge in skill_edges if edge.relation == "duplicates"]
    related_density_limit = max(50, len(records) * 3)
    return {
        "relation_counts_before_pruning": _relation_counts_before_pruning(relation_counts, workflow_pruning),
        "relation_counts": dict(sorted(relation_counts.items())),
        "invalid_relations": invalid_relations,
        "missing_reasons": missing_reasons,
        "isolated_skills": isolated,
        "workflow_next_edges": workflow_edges,
        "workflow_density_limit": density_limit,
        "workflow_density_warning": workflow_edges > density_limit,
        "duplicate_edges": duplicate_edges,
        "conflict_edges": conflict_edges,
        "skill_count": len(skill_ids),
        "top_high_degree_skills": top_high_degree,
        "workflow_pruning": _workflow_pruning_summary(workflow_pruning, workflow_edges),
        "related_edge_count": len(related_edges),
        "related_density_limit": related_density_limit,
        "related_density_warning": len(related_edges) > related_density_limit,
        "top_high_degree_related_nodes": _top_high_degree_related(records, related_edges),
        "conflict_edge_examples": conflict_edge_list[:10],
        "duplicate_edge_examples": duplicate_edge_list[:10],
        "downgraded_related_examples": [edge for edge in related_edges if "downgraded" in edge.reason.lower()][:10],
        "suspicious_related_edges": _suspicious_related_edges(related_edges),
    }


def _relation_counts_before_pruning(relation_counts: dict[str, int], workflow_pruning: object | None) -> dict[str, int]:
    before = dict(relation_counts)
    if workflow_pruning is None:
        return dict(sorted(before.items()))
    summary = _workflow_pruning_summary(workflow_pruning, relation_counts.get("workflow_next", 0))
    downgraded = int(summary["downgraded_to_related_count"])
    before["workflow_next"] = int(summary["before_count"])
    before["related"] = max(0, before.get("related", 0) - downgraded)
    return dict(sorted(before.items()))


def _workflow_pruning_summary(workflow_pruning: object | None, workflow_edges: int) -> dict[str, object]:
    if workflow_pruning is None:
        return {
            "before_count": workflow_edges,
            "after_count": workflow_edges,
            "downgraded_to_related_count": 0,
            "removed_workflow_edges_count": 0,
            "downgraded_to_related_examples": [],
            "removed_workflow_edge_examples": [],
        }
    downgraded = list(getattr(workflow_pruning, "downgraded_to_related", []))
    removed = list(getattr(workflow_pruning, "removed_workflow_edges", []))
    return {
        "before_count": int(getattr(workflow_pruning, "before_count", workflow_edges)),
        "after_count": int(getattr(workflow_pruning, "after_count", workflow_edges)),
        "downgraded_to_related_count": len(downgraded),
        "removed_workflow_edges_count": len(removed),
        "downgraded_to_related_examples": downgraded[:10],
        "removed_workflow_edge_examples": removed[:10],
    }


def _top_high_degree(records: list[SkillRecord], edges: list[GraphEdge], limit: int = 10) -> list[tuple[SkillRecord, int, int, int]]:
    by_id = {record.id: record for record in records}
    outgoing: dict[str, int] = defaultdict(int)
    incoming: dict[str, int] = defaultdict(int)
    workflow_outgoing: dict[str, int] = defaultdict(int)
    for edge in edges:
        if edge.source in by_id:
            outgoing[edge.source] += 1
            if edge.relation == "workflow_next":
                workflow_outgoing[edge.source] += 1
        if edge.target in by_id:
            incoming[edge.target] += 1
    rows = [
        (record, outgoing.get(record.id, 0), incoming.get(record.id, 0), workflow_outgoing.get(record.id, 0))
        for record in records
    ]
    rows.sort(key=lambda item: (-(item[1] + item[2]), -item[3], item[0].name.lower()))
    return rows[:limit]


def _top_high_degree_related(records: list[SkillRecord], edges: list[GraphEdge], limit: int = 10) -> list[tuple[SkillRecord, int, int, int]]:
    by_id = {record.id: record for record in records}
    outgoing: dict[str, int] = defaultdict(int)
    incoming: dict[str, int] = defaultdict(int)
    for edge in edges:
        if edge.source in by_id:
            outgoing[edge.source] += 1
        if edge.target in by_id:
            incoming[edge.target] += 1
    rows = [(record, outgoing.get(record.id, 0), incoming.get(record.id, 0), 0) for record in records]
    rows.sort(key=lambda item: (-(item[1] + item[2]), item[0].name.lower()))
    return rows[:limit]


def _suspicious_related_edges(edges: list[GraphEdge]) -> list[GraphEdge]:
    strong_reason_terms = ["same package", "downgraded", "duplicate", "conflict", "fallback"]
    return [
        edge
        for edge in edges
        if not any(term in edge.reason.lower() for term in strong_reason_terms)
    ][:10]


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


def _conflict_reasons(left: SkillRecord, right: SkillRecord) -> list[str]:
    reasons: list[str] = []
    if _norm_name(left.name) == _norm_name(right.name):
        reasons.append("same normalized name")
    elif _jaccard(_tokens(left.name), _tokens(right.name)) >= 0.5:
        reasons.append("similar name")
    if left.input_types and left.input_types == right.input_types and left.output_types and left.output_types == right.output_types:
        reasons.append("same input/output types")
    if _jaccard(_tokens(left.description), _tokens(right.description)) >= 0.45:
        reasons.append("similar description")
    return reasons


def _norm_name(value: str) -> str:
    return " ".join(_tokens(value))


def _tokens(value: str) -> set[str]:
    stop = {"skill", "reference", "the", "and", "for", "with", "from", "use"}
    return {token for token in re.findall(r"[a-z0-9]+", value.lower()) if len(token) > 2 and token not in stop}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)
