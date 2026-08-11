from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import re

from .models import GRAPH_RELATIONS, GraphEdge, SkillRecord
from .reports import conflict_candidates
from .search import ngrams, tokens


MVP_RELATIONS = GRAPH_RELATIONS
MAX_WORKFLOW_NEXT_PER_SOURCE = 3
WORKFLOW_KEEP_THRESHOLD = 0.82
RELATED_DOWNGRADE_THRESHOLD = 0.68


@dataclass(frozen=True)
class WorkflowCandidate:
    edge: GraphEdge
    score: float
    downgrade_edge: GraphEdge | None = None


@dataclass(frozen=True)
class WorkflowPruningReport:
    before_count: int
    after_count: int
    downgraded_to_related: list[GraphEdge]
    removed_workflow_edges: list[GraphEdge]
    retained_workflow_edges: list[GraphEdge]


def build_skill_edges(records: list[SkillRecord]) -> list[GraphEdge]:
    edges, _ = build_skill_edges_with_pruning(records)
    return edges


def build_skill_edges_with_pruning(
    records: list[SkillRecord],
    max_workflow_next_per_source: int = MAX_WORKFLOW_NEXT_PER_SOURCE,
) -> tuple[list[GraphEdge], WorkflowPruningReport]:
    edges: list[GraphEdge] = []
    by_reference = _reference_map(records)
    for skill in records:
        if skill.risk_level == "high" and skill.package != "safety-governance":
            edges.append(GraphEdge(
                "package:safety-governance", skill.id, "governs", "explicit high-risk policy",
                0.95, "policy", "confirmed", str(skill.provenance.get("risk_level", {}).get("evidence", "explicit risk policy")),
            ))
        for relation, references in (("requires", skill.requires), ("conflicts_with", skill.conflicts), ("workflow_next", skill.workflow_next)):
            for reference in references:
                target = by_reference.get(reference.strip().lower())
                if target and target.id != skill.id:
                    edges.append(GraphEdge(
                        skill.id, target.id, relation, f"explicit {relation} metadata",
                        1.0, "explicit-metadata", "confirmed", f"{skill.name}.{relation}: {reference}",
                    ))
        for relation, references in (
            ("related", skill.related_candidates), ("workflow_next", skill.workflow_candidates),
            ("duplicates", skill.duplicate_candidates), ("conflicts_with", skill.conflict_candidates),
        ):
            for reference in references:
                target = by_reference.get(reference.strip().lower())
                if target and target.id != skill.id:
                    edges.append(GraphEdge(
                        skill.id, target.id, relation, f"approved enrichment {relation} proposal",
                        0.75, "approved-enrichment", "candidate", f"proposal target: {reference}",
                    ))
        if not skill.package:
            edges.append(GraphEdge(
                "inbox:needs-review", skill.id, "governs", "unclassified skill requires review",
                1.0, "policy", "confirmed", "package field is empty",
            ))

    workflow_candidates = _workflow_candidates(records)
    retained, removed = _retain_workflow_candidates(workflow_candidates, max_workflow_next_per_source)
    edges.extend(retained)
    edges.extend(_related_candidates(records))
    edges.extend(_duplicate_candidates(records))
    edges.extend(_conflict_candidates(records))
    pruning = WorkflowPruningReport(
        before_count=len(workflow_candidates), after_count=len(retained),
        downgraded_to_related=[], removed_workflow_edges=removed,
        retained_workflow_edges=retained,
    )
    return _dedupe_edges(edges), pruning


def build_package_edges(records: list[SkillRecord]) -> list[GraphEdge]:
    packages = sorted({record.package for record in records if record.package})
    return [
        GraphEdge("safety-governance", package, "governs", "package contains an explicitly high-risk skill", 0.9, "policy")
        for package in packages
        if package != "safety-governance" and any(record.package == package and record.risk_level == "high" for record in records)
    ]


def _workflow_candidates(records: list[SkillRecord]) -> list[WorkflowCandidate]:
    by_input: dict[str, list[SkillRecord]] = defaultdict(list)
    for record in records:
        for value in record.input_types:
            by_input[value.lower()].append(record)
    candidates: list[WorkflowCandidate] = []
    for left in records:
        for output in {value.lower() for value in left.output_types}:
            for right in by_input.get(output, []):
                if left.id == right.id:
                    continue
                directional = _directional_evidence(left, right)
                if not directional:
                    continue
                score = 0.82 + min((left.confidence + right.confidence) * 0.04, 0.08)
                edge = GraphEdge(
                    left.id, right.id, "workflow_next", f"output→input contract: {output}; {directional}",
                    round(min(score, 0.99), 2), "typed-inference", "confirmed",
                    f"{left.name}.output_types[{output}] -> {right.name}.input_types[{output}]",
                )
                candidates.append(WorkflowCandidate(edge, score))
    return candidates


def _retain_workflow_candidates(candidates: list[WorkflowCandidate], max_per_source: int) -> tuple[list[GraphEdge], list[GraphEdge]]:
    grouped: dict[str, list[WorkflowCandidate]] = defaultdict(list)
    for candidate in candidates:
        grouped[candidate.edge.source].append(candidate)
    retained: list[GraphEdge] = []
    removed: list[GraphEdge] = []
    for source, values in grouped.items():
        ordered = sorted(values, key=lambda item: (-item.score, item.edge.target))
        retained.extend(item.edge for item in ordered[:max_per_source] if item.score >= WORKFLOW_KEEP_THRESHOLD)
        for item in ordered[max_per_source:]:
            removed.append(GraphEdge(
                item.edge.source, item.edge.target, item.edge.relation,
                f"removed after typed top-N pruning: {item.edge.reason}", item.edge.confidence,
                item.edge.origin, "candidate", item.edge.evidence,
            ))
    return retained, removed


def _related_candidates(records: list[SkillRecord], k: int = 3) -> list[GraphEdge]:
    neighbors: dict[str, list[tuple[float, SkillRecord]]] = defaultdict(list)
    for left in records:
        left_tokens = set(tokens(_routing_text(left)))
        for right in records:
            if left.id == right.id:
                continue
            right_tokens = set(tokens(_routing_text(right)))
            score = _jaccard(left_tokens, right_tokens)
            if score >= 0.25:
                neighbors[left.id].append((score, right))
        neighbors[left.id].sort(key=lambda item: (-item[0], item[1].id))
        neighbors[left.id] = neighbors[left.id][:k]
    top = {source: {record.id for _, record in values} for source, values in neighbors.items()}
    edges: list[GraphEdge] = []
    seen: set[tuple[str, str]] = set()
    for left in records:
        for score, right in neighbors[left.id]:
            if left.id not in top.get(right.id, set()):
                continue
            pair = tuple(sorted((left.id, right.id)))
            if pair in seen:
                continue
            seen.add(pair)
            edges.append(GraphEdge(
                left.id, right.id, "related", "mutual-kNN routing metadata similarity",
                round(score, 2), "mutual-knn", "candidate", "name/description/when_to_use token overlap",
            ))
    return edges


def _duplicate_candidates(records: list[SkillRecord]) -> list[GraphEdge]:
    edges: list[GraphEdge] = []
    for index, left in enumerate(records):
        for right in records[index + 1:]:
            left_name, right_name = ngrams(left.name), ngrams(right.name)
            similarity = _jaccard(left_name, right_name)
            if _normalized_name(left.name) == _normalized_name(right.name) or similarity >= 0.8:
                edges.append(GraphEdge(
                    left.id, right.id, "duplicates", "possible duplicate name and routing surface",
                    round(max(0.8, similarity), 2), "name-similarity", "candidate", f"{left.name} <> {right.name}",
                ))
    return edges


def _conflict_candidates(records: list[SkillRecord]) -> list[GraphEdge]:
    return [
        GraphEdge(left.id, right.id, "conflicts_with", reason, 0.55, "heuristic", "candidate", reason)
        for left, right, reason in conflict_candidates(records)
        if _normalized_name(left.name) != _normalized_name(right.name)
    ]


def _directional_evidence(left: SkillRecord, right: SkillRecord) -> str:
    left_text = _routing_text(left).lower()
    right_text = _routing_text(right).lower()
    right_names = [right.name.lower(), *(value.lower() for value in right.aliases)]
    left_names = [left.name.lower(), *(value.lower() for value in left.aliases)]
    if any(name and any(phrase in left_text for phrase in (f"before {name}", f"then {name}", f"for {name}", f"handoff to {name}")) for name in right_names):
        return "source metadata names the downstream skill"
    if any(name and any(phrase in right_text for phrase in (f"after {name}", f"requires {name}", f"from {name}", f"following {name}")) for name in left_names):
        return "target metadata names the upstream skill"
    return ""


def _reference_map(records: list[SkillRecord]) -> dict[str, SkillRecord]:
    ids = {record.id.strip().lower(): record for record in records}
    candidates: dict[str, list[SkillRecord]] = defaultdict(list)
    for record in records:
        for value in (record.name, *record.aliases):
            if value:
                candidates[value.strip().lower()].append(record)
    result = dict(ids)
    for reference, matches in candidates.items():
        unique = {record.id: record for record in matches}
        if reference not in ids and len(unique) == 1:
            result[reference] = next(iter(unique.values()))
    return result


def _routing_text(record: SkillRecord) -> str:
    return " ".join([record.name, record.description, record.when_to_use, *record.aliases])


def _normalized_name(value: str) -> str:
    return " ".join(tokens(value))


def _dedupe_edges(edges: list[GraphEdge]) -> list[GraphEdge]:
    unique: dict[tuple[str, str, str, str], GraphEdge] = {}
    for edge in edges:
        if edge.relation in MVP_RELATIONS:
            unique[(edge.source, edge.target, edge.relation, edge.review_state)] = edge
    return sorted(unique.values(), key=lambda item: (item.source, item.target, item.relation, item.review_state))


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)
