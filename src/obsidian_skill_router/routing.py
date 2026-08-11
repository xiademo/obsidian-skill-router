from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
import re
from pathlib import Path, PurePosixPath
import stat
from typing import Any, Iterable

from .classify import RISK_RANK, infer_action_risk
from .models import GRAPH_RELATIONS, GraphEdge, SkillRecord
from .search import exact_named, hybrid_rank, normalize, policy_exclusions, tokens as search_tokens
from .validate import EDGE_FIELDS, SKILL_FIELDS, TOP_FIELDS


@dataclass(frozen=True)
class PackageCandidate:
    package: str
    score: float
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class RankedResult:
    records: list[SkillRecord]
    scored_records: list[tuple[float, SkillRecord]]
    package_candidates: list[PackageCandidate]
    package_evidence: dict[str, tuple[str, ...]]
    normalized_terms: tuple[str, ...]
    constraint_detected: bool
    sequential_task: bool
    strategy: str = "legacy"
    evidence: dict[str, tuple[str, ...]] = field(default_factory=dict)
    excluded_candidates: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RouteDecision:
    status: str
    reason: str
    primary: SkillRecord | None
    primary_score: float
    package_candidates: list[PackageCandidate]
    related_packages: list[str]
    constraint_detected: bool
    alternatives: list[tuple[float, SkillRecord]]
    explain: bool
    decision_type: str = "recommend"
    reason_code: str = "matched"
    evidence: tuple[str, ...] = ()
    plan: tuple[dict[str, Any], ...] = ()
    policy: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    excluded_candidates: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        primary = _candidate_payload(self.primary, self.primary_score) if self.primary else None
        alternatives = [_candidate_payload(record, score) for score, record in self.alternatives] if self.explain else []
        payload: dict[str, Any] = {
            "status": self.status,
            "reason": self.reason,
            "primary": primary,
            "package_candidates": [
                {"package": item.package, "score": item.score, "evidence": list(item.evidence)}
                for item in self.package_candidates[:2]
            ],
            "related_packages": self.related_packages,
            "constraint_detected": self.constraint_detected,
            "alternatives": alternatives,
            "decision": {
                "type": self.decision_type,
                "reason_code": self.reason_code,
                "clarification": _clarification(self) if self.decision_type == "clarify_required" else None,
            },
            "reason_code": self.reason_code,
            "evidence": list(self.evidence),
            "excluded_candidates": [
                {"id": skill_id, "reason_code": reason} for skill_id, reason in sorted(self.excluded_candidates.items())
            ],
            "policy": self.policy,
            "plan": list(self.plan),
            "warnings": list(self.warnings),
        }
        rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        candidate_payload = json.dumps(
            {"primary": primary, "alternatives": alternatives, "plan": list(self.plan)},
            ensure_ascii=False,
            sort_keys=True,
        )
        payload["budget"] = {
            "response_tokens_estimate": _estimate_tokens(rendered),
            "candidate_payload_tokens_estimate": _estimate_tokens(candidate_payload) if primary or alternatives or self.plan else 0,
            "expanded": self.explain,
            "response_chars": len(rendered),
            "response_bytes": len(rendered.encode("utf-8")),
        }
        return payload


CHINESE_QUERY_SIGNALS: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("\u7b80\u5386", "resume", ("career-resume",), ("resume", "career")),
    ("jd", "job description", ("career-resume",), ("job", "resume")),
    ("\u5c97\u4f4d", "job role", ("career-resume",), ("job", "role")),
    ("\u9762\u8bd5", "interview", ("career-resume",), ("interview", "resume")),
    ("\u6c42\u804c", "career", ("career-resume",), ("career", "resume")),
    ("pdf", "pdf", ("document-processing",), ("pdf",)),
    ("word", "word docx", ("document-processing",), ("word", "docx")),
    ("\u6587\u6863", "document", ("document-processing",), ("document", "doc")),
    ("\u6587\u4ef6", "file document", ("document-processing",), ("file", "document")),
    ("\u62a5\u544a", "report", ("research-writing", "document-processing"), ("report",)),
    ("\u5bfc\u51fa", "export", ("document-processing",), ("export",)),
    ("\u9875\u7801", "page", ("document-processing",), ("page", "pdf")),
    ("ocr", "ocr", ("document-processing",), ("ocr", "pdf")),
    ("\u8bc6\u522b", "ocr extract", ("document-processing",), ("ocr", "extract")),
    ("\u590d\u5236\u6587\u5b57", "selectable text", ("document-processing",), ("text", "extract")),
    ("\u4ee3\u7801", "code", ("coding-workflow",), ("code",)),
    ("\u9879\u76ee", "repo project", ("coding-workflow",), ("repo", "project")),
    ("\u6d4b\u8bd5", "tests", ("coding-workflow",), ("tests", "test")),
    ("\u91cd\u6784", "refactor", ("coding-workflow",), ("refactor", "code")),
    ("debug", "debug", ("coding-workflow",), ("debug", "code")),
    ("git", "git", ("coding-workflow",), ("git", "repo")),
    ("\u8c03\u7814", "research", ("research-writing",), ("research",)),
    ("\u8bba\u6587", "paper", ("research-writing",), ("paper", "research")),
    ("\u5f15\u7528", "citation", ("research-writing",), ("citation", "research")),
    ("\u641c\u7d22", "search", ("research-writing",), ("search", "research")),
    ("\u8d44\u6599", "material notes", ("research-writing", "document-processing"), ("material", "notes")),
    ("\u77e5\u8bc6\u5e93", "knowledge base obsidian", ("memory-knowledge",), ("knowledge", "obsidian", "note")),
    ("obsidian", "obsidian", ("memory-knowledge",), ("obsidian", "note")),
    ("\u7b14\u8bb0", "note", ("memory-knowledge",), ("note",)),
    ("\u8bb0\u5fc6", "memory", ("memory-knowledge",), ("memory", "knowledge")),
    ("\u5f52\u6863", "archive", ("memory-knowledge",), ("archive", "note")),
    ("\u98ce\u9669", "risk", ("safety-governance",), ("risk", "safety")),
    ("\u6743\u9650", "permission", ("safety-governance",), ("permission", "risk")),
    ("token", "token", ("safety-governance",), ("token", "secret")),
    ("secret", "secret", ("safety-governance",), ("secret",)),
    ("\u51ed\u8bc1", "credential", ("safety-governance",), ("credential", "secret")),
    ("\u6ce8\u5165", "injection", ("safety-governance",), ("injection", "risk")),
    ("\u81ea\u52a8\u5316", "automation", ("automation-ops",), ("automation",)),
    ("\u90ae\u4ef6", "email", ("automation-ops",), ("email",)),
    ("\u6d4f\u89c8\u5668", "browser", ("automation-ops",), ("browser",)),
    ("\u6279\u5904\u7406", "batch automation", ("automation-ops",), ("batch", "automation")),
    ("\u5de5\u5177", "tool index", ("automation-ops", "memory-knowledge"), ("tool", "index")),
    ("\u6574\u7406", "organize", ("document-processing", "memory-knowledge"), ("organize",)),
    ("\u770b\u770b", "review", ("coding-workflow", "safety-governance"), ("review",)),
    ("\u6709\u6ca1\u6709\u95ee\u9898", "review risk issue", ("coding-workflow", "safety-governance"), ("review", "risk")),
    ("\u5206\u7c7b", "classify skill risk", ("safety-governance",), ("classify", "skill", "risk")),
    ("skill", "skill", ("safety-governance",), ("skill",)),
)

CONSTRAINT_PHRASES = (
    "\u53ea",
    "\u4e0d\u8981",
    "\u4e0d\u9700\u8981",
    "\u65e0\u9700",
    "\u4ec5",
    "\u4e0d\u662f",
    "only",
    "do not",
    "don't",
    "no workflow",
    "no follow-up",
    "no follow up",
)

SEQUENCE_PHRASES = ("\u7136\u540e", "\u518d", "\u4e4b\u540e", "then", "after", "before", "next", "handoff", "pipeline")
NEGATION_PHRASES = ("\u4e0d\u8981", "\u4e0d\u9700\u8981", "\u65e0\u9700", "\u4e0d\u662f", "do not", "don't", "not ")
AMBIGUOUS_INTENT_PHRASES = (
    "\u6574\u7406",
    "\u770b\u770b",
    "\u6709\u6ca1\u6709\u95ee\u9898",
    "\u80fd\u4e0d\u80fd\u53d8\u6210",
    "help me organize",
    "look at this project",
    "can this become",
    "can this",
    "\u5de5\u5177\u7ba1\u7406",
    "manage local tools",
)
ACTION_TERM_SIGNALS = (
    ("\u63d0\u53d6", ("extract",)),
    ("\u4fee\u6539", ("rewrite", "tailor")),
    ("\u5206\u6790", ("analyze", "analysis")),
    ("\u751f\u6210", ("generate", "create")),
    ("\u68c0\u67e5", ("check", "review")),
    ("\u603b\u7ed3", ("summarize", "summary")),
    ("\u5f52\u6863", ("archive",)),
)


def rank_records(query: str, records: list[SkillRecord], *, strategy: str = "legacy", profile: str = "") -> RankedResult:
    if strategy in {"hybrid", "hybrid-graph"}:
        ranked = hybrid_rank(query, records, profile=profile)
        package_scores: dict[str, float] = defaultdict(float)
        package_reasons: dict[str, list[str]] = defaultdict(list)
        for score, record, reasons in ranked:
            if record.package:
                package_scores[record.package] = max(package_scores[record.package], score)
                package_reasons[record.package].extend(reasons[:2])
        candidates = [
            PackageCandidate(package, round(score, 4), tuple(dict.fromkeys(package_reasons[package]))[:6])
            for package, score in package_scores.items()
        ]
        candidates.sort(key=lambda item: (-item.score, item.package))
        return RankedResult(
            records=[record for _, record, _ in ranked],
            scored_records=[(score, record) for score, record, _ in ranked],
            package_candidates=candidates,
            package_evidence={candidate.package: candidate.evidence for candidate in candidates},
            normalized_terms=tuple(sorted(set(search_tokens(query)))),
            constraint_detected=has_constraint(query),
            sequential_task=any(marker in normalize(query) for marker in SEQUENCE_PHRASES),
            strategy=strategy,
            evidence={record.id: reasons for _, record, reasons in ranked},
            excluded_candidates=(
                policy_exclusions(query, records, profile=profile)
                if any(
                    record.requires_explicit_user_intent
                    or not record.default_routable
                    or record.route_mode in {"manual", "explicit-only", "profile-only"}
                    or record.trigger_mode not in {"", "default", "direct-or-semantic-match"}
                    or bool(record.compatibility and profile)
                    or bool(record.negative_triggers)
                    for record in records
                )
                else {}
            ),
        )

    exclusions = policy_exclusions(query, records, profile=profile)
    eligible_records = [record for record in records if record.id not in exclusions]
    query_terms, package_evidence, constraint_detected = query_context(query)
    scored: list[tuple[float, SkillRecord]] = []
    package_scores: dict[str, float] = defaultdict(float)
    package_reasons: dict[str, list[str]] = {package: list(reasons) for package, reasons in package_evidence.items()}
    for package, reasons in package_evidence.items():
        package_scores[package] += len(reasons) * 1.5

    for record in eligible_records:
        if _requires_named_intent(record) and not _has_named_intent(record, query):
            continue
        text = " ".join([record.name, record.description, record.when_to_use, " ".join(record.input_types), " ".join(record.output_types), record.package])
        overlap = len(query_terms & terms(text))
        package_bonus = len(package_evidence.get(record.package, ())) * 1.25 if record.package else 0.0
        if overlap == 0 and package_bonus == 0:
            continue
        score = overlap + package_bonus + (0.25 * record.confidence)
        if record.package and record.package.replace("-", " ") in query.lower():
            score += 1.0
        scored.append((score, record))
        if record.package:
            package_scores[record.package] = max(package_scores.get(record.package, 0.0), score)
            if overlap:
                package_reasons.setdefault(record.package, []).append(f"matched {overlap} normalized terms")

    scored.sort(key=lambda item: (-item[0], item[1].name.lower(), item[1].id))
    candidates = [
        PackageCandidate(package, round(score, 2), tuple(dict.fromkeys(package_reasons.get(package, [])))[:6])
        for package, score in package_scores.items()
        if score > 0
    ]
    candidates.sort(key=lambda item: (-item.score, item.package))
    return RankedResult(
        records=[record for _, record in scored],
        scored_records=scored,
        package_candidates=candidates,
        package_evidence={candidate.package: candidate.evidence for candidate in candidates},
        normalized_terms=tuple(sorted(query_terms)),
        constraint_detected=constraint_detected,
        sequential_task=any(marker in query.lower() for marker in SEQUENCE_PHRASES),
        strategy="legacy",
        excluded_candidates=exclusions,
    )


def route_records(
    query: str,
    records: list[SkillRecord],
    *,
    explain: bool = False,
    strategy: str = "legacy",
    profile: str = "",
    edges: list[GraphEdge] | None = None,
    allow_plan: bool = True,
    warnings: tuple[str, ...] = (),
) -> RouteDecision:
    if _is_unsupported_task(query):
        return RouteDecision(
            "continue_without_skill", "Task requests an unsupported or irreversible external action.", None, 0.0,
            [], [], has_constraint(query), [], explain, decision_type="continue_without_skill",
            reason_code="unsupported_action", policy={"blocked": True}, warnings=warnings,
        )

    if allow_plan and strategy in {"hybrid", "hybrid-graph"}:
        clauses = _split_task_v2(query)
        if len(clauses) > 1:
            candidate_sets: list[list[tuple[float, SkillRecord]]] = []
            for index, clause in enumerate(clauses, 1):
                if _explicit_skill_request(clause):
                    named = [record for record in records if exact_named(record, clause)]
                    if not named:
                        return RouteDecision(
                            "continue_without_skill", "A named skill in the multi-step task is not present in the local registry.", None, 0.0,
                            [], [], has_constraint(query), [], explain,
                            decision_type="clarify_required", reason_code="explicit_skill_not_found",
                            evidence=(f"step {index}: {clause}",), warnings=warnings,
                        )
                    exclusions = policy_exclusions(clause, named, profile=profile)
                    if exclusions and len(exclusions) == len(named):
                        return RouteDecision(
                            "continue_without_skill", "A named skill is blocked by its routing policy.", None, 0.0,
                            [], [], has_constraint(query), [], explain,
                            decision_type="clarify_required", reason_code="explicit_skill_policy_blocked",
                            evidence=(f"step {index}: {clause}",), warnings=warnings,
                            excluded_candidates=exclusions,
                        )
                ranked_step = rank_records(clause, records, strategy=strategy, profile=profile)
                if strategy == "hybrid-graph" and edges and not ranked_step.constraint_detected:
                    ranked_step = _apply_graph_lift(ranked_step, edges, records)
                viable = [item for item in ranked_step.scored_records[:5] if item[0] >= 1.8]
                if not viable:
                    return RouteDecision(
                        "continue_without_skill", "A multi-step task contains an unsupported step.", None, 0.0,
                        ranked_step.package_candidates, [], has_constraint(query), [], explain,
                        decision_type="clarify_required", reason_code="plan_step_unsupported",
                        evidence=(f"step {index}: {clause}",), warnings=warnings,
                        excluded_candidates=ranked_step.excluded_candidates,
                    )
                exact_viable = [item for item in viable if exact_named(item[1], clause)]
                if exact_viable:
                    viable = exact_viable
                elif len(viable) > 1 and viable[0][0] - viable[1][0] <= 0.12:
                    return RouteDecision(
                        "continue_without_skill", "A multi-step task contains an ambiguous step.", None, 0.0,
                        ranked_step.package_candidates, [], has_constraint(query), viable[:2], explain,
                        decision_type="clarify_required", reason_code="plan_step_ambiguous",
                        evidence=(f"step {index}: {clause}",), warnings=warnings,
                        excluded_candidates=ranked_step.excluded_candidates,
                    )
                candidate_sets.append(viable)
            chosen_ids = _minimum_cost_cover(candidate_sets) if len(clauses) <= 8 else _greedy_cover(candidate_sets)
            selected: list[SkillRecord] = []
            steps: list[dict[str, Any]] = []
            review = False
            for index, (clause, candidates) in enumerate(zip(clauses, candidate_sets), 1):
                score, skill = next((item for item in candidates if item[1].id in chosen_ids), candidates[0])
                selected.append(skill)
                review = review or _requires_review(skill)
                steps.append({"original_step": index, "task": clause, "skill": _candidate_payload(skill, score)})
            selected = list(dict((skill.id, skill) for skill in selected).values())
            original_order = [skill.id for skill in selected]
            plan_edges = list(edges or [])
            plan_edges.extend(
                GraphEdge(
                    source,
                    target,
                    "workflow_next",
                    "explicit task sequence",
                    1.0,
                    "query-sequence",
                    "confirmed",
                    "ordered clauses in the user task",
                    "0.2",
                )
                for source, target in zip(original_order, original_order[1:])
                if source != target
            )
            selected, dependency_issue, dependency_policy_review = _expand_required_skills(
                selected, records, plan_edges, query=query, profile=profile
            )
            if dependency_issue:
                issue_code = (
                    "missing_required_dependency"
                    if "missing skill" in dependency_issue
                    else "ambiguous_required_dependency"
                    if "ambiguous required skill" in dependency_issue
                    else "dependency_policy_blocked"
                )
                return RouteDecision(
                    "review_required", "A confirmed required dependency cannot be added safely.", selected[0], 0.0,
                    [], [], has_constraint(query), [], explain, decision_type="review_required",
                    reason_code=issue_code, plan=tuple(steps),
                    evidence=(dependency_issue,), warnings=warnings,
                )
            step_ids = {str((step.get("skill") or {}).get("id", "")) for step in steps}
            for dependency in selected:
                if dependency.id not in step_ids:
                    steps.append(
                        {
                            "original_step": None,
                            "task": "Confirmed required dependency",
                            "skill": _candidate_payload(dependency, 0.0),
                        }
                    )
            review = review or dependency_policy_review or any(_requires_review(skill) for skill in selected)
            ordered, cycle = _order_plan(selected, plan_edges)
            if cycle:
                return RouteDecision(
                    "review_required", "Confirmed workflow dependencies contain a cycle.", selected[0], 0.0,
                    [], [], has_constraint(query), [], explain, decision_type="review_required",
                    reason_code="workflow_cycle", plan=tuple(steps), warnings=warnings,
                )
            by_id = {record.id: position for position, record in enumerate(ordered)}
            steps.sort(key=lambda item: by_id.get(str((item["skill"] or {}).get("id", "")), len(by_id)))
            for execution_step, step in enumerate(steps, 1):
                step["step"] = execution_step
            ordered_original = [skill_id for skill_id in (record.id for record in ordered) if skill_id in original_order]
            order_adjusted = ordered_original != original_order
            review = review or order_adjusted
            primary = ordered[0] if ordered else selected[0]
            return RouteDecision(
                "review_required" if review else "recommend", "Task decomposed into a deterministic local skill plan.",
                primary, 0.0, [], [], has_constraint(query), [], explain,
                decision_type="review_required" if review else "skill_plan",
                reason_code=(
                    "dependency_order_adjusted" if order_adjusted
                    else "dependency_policy_review" if dependency_policy_review
                    else "multi_step_plan"
                ),
                plan=tuple(steps),
                policy={
                    "requires_review": review,
                    "dependency_order_adjusted": order_adjusted,
                    "dependency_policy_review": dependency_policy_review,
                },
                warnings=warnings,
            )

    if strategy in {"hybrid", "hybrid-graph"} and _explicit_skill_request(query):
        named = [record for record in records if exact_named(record, query)]
        if not named:
            return RouteDecision(
                "continue_without_skill", "The explicitly named skill is not present in the local registry.", None, 0.0,
                [], [], has_constraint(query), [], explain,
                decision_type="continue_without_skill", reason_code="explicit_skill_not_found", warnings=warnings,
            )
        exclusions = policy_exclusions(query, named, profile=profile)
        if exclusions and len(exclusions) == len(named):
            return RouteDecision(
                "continue_without_skill", "The explicitly named skill is blocked by its routing policy.", None, 0.0,
                [], [], has_constraint(query), [], explain,
                decision_type="continue_without_skill", reason_code="explicit_skill_policy_blocked",
                warnings=warnings, excluded_candidates=exclusions,
            )

    ranked = rank_records(query, records, strategy=strategy, profile=profile)
    if strategy == "hybrid-graph" and edges and not ranked.constraint_detected:
        ranked = _apply_graph_lift(ranked, edges, records)
    if not ranked.scored_records:
        return RouteDecision(
            "continue_without_skill", "No local skill has enough task evidence.", None, 0.0,
            ranked.package_candidates, [], ranked.constraint_detected, [], explain,
            decision_type="continue_without_skill", reason_code="no_candidate", warnings=warnings,
            excluded_candidates=ranked.excluded_candidates,
        )

    top_score, top = ranked.scored_records[0]
    package_ambiguous = _package_ambiguous(ranked)
    skill_ambiguous = _skill_ambiguous(ranked)
    exact = exact_named(top, query)
    threshold = 1.8 if strategy in {"hybrid", "hybrid-graph"} else 2.0
    margin_ambiguous = False
    if strategy in {"hybrid", "hybrid-graph"} and len(ranked.scored_records) > 1 and not exact:
        margin_ambiguous = (top_score - ranked.scored_records[1][0]) <= 0.12
    if top_score < threshold:
        return RouteDecision(
            "continue_without_skill", "No local skill has enough task evidence.", None, 0.0,
            ranked.package_candidates, [], ranked.constraint_detected, [], explain,
            decision_type="continue_without_skill", reason_code="insufficient_evidence",
            evidence=ranked.evidence.get(top.id, ()), warnings=warnings,
            excluded_candidates=ranked.excluded_candidates,
        )
    ambiguous = (((package_ambiguous or _underspecified_intent(query, ranked)) and not ranked.sequential_task) or skill_ambiguous or margin_ambiguous)
    if ranked.constraint_detected and len(ranked.scored_records) > 1:
        ambiguous = (top_score - ranked.scored_records[1][0]) <= 0.02
    if not exact and ambiguous:
        reason = "Task evidence is ambiguous; continue without loading a skill."
        return RouteDecision(
            "continue_without_skill", reason, None, 0.0, ranked.package_candidates, [], ranked.constraint_detected,
            ranked.scored_records[:2], explain,
            decision_type="clarify_required" if ranked.scored_records else "continue_without_skill",
            reason_code="ambiguous_candidates" if ranked.scored_records else "insufficient_evidence",
            evidence=ranked.evidence.get(top.id, ()), warnings=warnings,
            excluded_candidates=ranked.excluded_candidates,
        )

    related_packages = _related_packages(ranked, top.package)
    requires_review = _requires_review(top)
    if requires_review:
        reason = "High-risk skill requires review before any manual invocation."
        status = "review_required"
    else:
        reason = "Primary skill has the strongest metadata overlap and package evidence."
        status = "recommend"
    alternatives = ranked.scored_records[1:3] if explain else []
    return RouteDecision(
        status, reason, top, top_score, ranked.package_candidates, related_packages, ranked.constraint_detected,
        alternatives, explain, decision_type="review_required" if requires_review else "recommend",
        reason_code="policy_review" if requires_review else "matched",
        evidence=ranked.evidence.get(top.id, ()),
        policy={"route_mode": top.route_mode, "risk_level": top.risk_level, "requires_review": requires_review},
        warnings=warnings,
        excluded_candidates=ranked.excluded_candidates,
    )


def routing_index_payload(records: Iterable[SkillRecord], edges: Iterable[GraphEdge]) -> dict[str, Any]:
    record_list = list(records)
    edge_list = list(edges)
    source_roots: dict[str, str] = {}
    for record in record_list:
        if record.source_root_id and record.source_path:
            path = Path(record.source_path)
            parts = Path(record.source_relative_path).parts if record.source_relative_path not in {"", "."} else ()
            root_path = path
            for _ in parts:
                root_path = root_path.parent
            source_roots.setdefault(record.source_root_id, str(root_path))
    return {
        "schema_version": "0.2",
        "compiler_version": "0.2",
        "rules_version": "0.2",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_roots": [
            {
                "id": root_id,
                "path": path,
                "skill_count": sum(1 for record in record_list if record.source_root_id == root_id),
                "content_digest": _root_content_digest(record_list, root_id),
            }
            for root_id, path in sorted(source_roots.items())
        ],
        "packages": sorted({record.package for record in record_list if record.package}),
        "managed_files_manifest": ".obsidian-skill-router/managed-files.json",
        "index_stats": {"skills": len(record_list), "edges": len(edge_list)},
        "skills": [
            {
                "id": record.id,
                "name": record.name,
                "package": record.package,
                "description": record.description,
                "when_to_use": record.when_to_use,
                "input_types": record.input_types,
                "output_types": record.output_types,
                "risk_level": record.risk_level,
                "confidence": record.confidence,
                "alternate_packages": record.alternate_packages,
                "source_root_id": record.source_root_id,
                "source_relative_path": record.source_relative_path,
                "source_hash": record.source_hash,
                "source_mtime": record.source_mtime,
                "last_scanned": record.last_scanned,
                "provider": record.provider,
                "compatibility": record.compatibility,
                "allowed_tools": record.allowed_tools,
                "aliases": record.aliases,
                "positive_triggers": record.positive_triggers,
                "negative_triggers": record.negative_triggers,
                "actions": record.actions,
                "artifacts": record.artifacts,
                "requires": record.requires,
                "conflicts": record.conflicts,
                "workflow_next": record.workflow_next,
                "related_candidates": record.related_candidates,
                "workflow_candidates": record.workflow_candidates,
                "duplicate_candidates": record.duplicate_candidates,
                "conflict_candidates": record.conflict_candidates,
                "route_mode": record.route_mode,
                "trigger_mode": record.trigger_mode,
                "default_routable": record.default_routable,
                "requires_explicit_user_intent": record.requires_explicit_user_intent,
                "topic_risk": record.topic_risk,
                "action_risk": record.action_risk,
                "side_effects": record.side_effects,
                "permissions": record.permissions,
                "permissions_known": record.permissions_known,
                "network_access": record.network_access,
                "writes_files": record.writes_files,
                "status": record.status,
                "review_state": record.review_state,
                "provenance": record.provenance,
                "card_path": _card_path(record),
            }
            for record in record_list
        ],
        "edges": [
            {
                "source": edge.source, "target": edge.target, "relation": edge.relation,
                "reason": edge.reason, "confidence": edge.confidence, "origin": edge.origin,
                "review_state": edge.review_state, "evidence": edge.evidence,
                "rule_version": edge.rule_version,
            }
            for edge in edge_list
        ],
    }


def _root_content_digest(records: list[SkillRecord], root_id: str) -> str:
    content = "".join(sorted(record.source_hash for record in records if record.source_root_id == root_id))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


ROUTING_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
ROUTING_PACKAGE_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
ROUTE_MODE_VALUES = {"default", "manual", "explicit-only", "profile-only"}
TRIGGER_MODE_VALUES = {
    "default", "direct-or-semantic-match", "strong-semantic-match",
    "explicit-only", "profile-only", "manual",
}
RISK_VALUES = {"low", "medium", "high", "unknown"}
TOPIC_RISK_VALUES = {"low-topic", "high-topic", "unknown"}
REVIEW_STATES = {"confirmed", "candidate", "rejected"}
CANDIDATE_EDGE_ORIGINS = {"approved-enrichment", "heuristic", "mutual-knn", "name-similarity"}
ROUTING_INDEX_MAX_BYTES = 64 * 1024 * 1024


def load_routing_index(vault: Path) -> tuple[list[SkillRecord], list[GraphEdge]]:
    payload, _, _ = _read_routing_payload(vault)
    return _decode_routing_payload(vault, payload)


def _read_routing_payload(vault: Path) -> tuple[dict[str, Any], Path, dict[str, str]]:
    root = vault.resolve()
    path = _safe_registry_vault_path(root, "00_Router/routing-index.json")
    if not path.is_file():
        raise ValueError(f"Routing index is missing: {path}")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ValueError(f"Cannot inspect routing index {path}: {exc}") from exc
    if size > ROUTING_INDEX_MAX_BYTES:
        raise ValueError(f"Routing index exceeds {ROUTING_INDEX_MAX_BYTES} bytes: {path}")
    try:
        index_bytes = path.read_bytes()
        payload = json.loads(index_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Malformed routing index {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") not in {"0.1", "0.2"}:
        raise ValueError(f"Unsupported routing index: {path}")
    if payload["schema_version"] == "0.2" and set(payload) - TOP_FIELDS:
        unknown = ", ".join(sorted(set(payload) - TOP_FIELDS))
        raise ValueError(f"Unknown routing index fields: {unknown}")
    manifest_path = _safe_registry_vault_path(root, ".obsidian-skill-router/managed-files.json")
    if manifest_path.is_file() and payload["schema_version"] != "0.2":
        raise ValueError("Managed Registry schema downgrade detected")
    managed_hashes: dict[str, str] = {}
    if payload["schema_version"] == "0.2":
        managed_hashes = _verify_registry_manifest(root, index_bytes)
    return payload, path, managed_hashes


def _verify_registry_manifest(vault: Path, index_bytes: bytes) -> dict[str, str]:
    path = _safe_registry_vault_path(vault, ".obsidian-skill-router/managed-files.json")
    if not path.is_file():
        raise ValueError(f"Managed-files manifest is missing: {path}")
    try:
        if path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError(f"Managed-files manifest exceeds 16777216 bytes: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Malformed managed-files manifest {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != "0.2" or not isinstance(payload.get("files"), list):
        raise ValueError(f"Malformed managed-files manifest: {path}")
    managed_hashes: dict[str, str] = {}
    for item in payload["files"]:
        if not isinstance(item, dict):
            raise ValueError(f"Malformed managed-files entry: {path}")
        relative = item.get("path")
        digest = item.get("sha256")
        if not isinstance(relative, str) or not relative or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Malformed path or hash in managed-files manifest")
        if relative in managed_hashes:
            raise ValueError(f"Duplicate managed-files entry: {relative}")
        managed_hashes[relative] = digest
    if "00_Router/routing-index.json" not in managed_hashes:
        raise ValueError("Managed-files manifest must contain exactly one routing-index entry")
    if hashlib.sha256(index_bytes).hexdigest() != managed_hashes["00_Router/routing-index.json"]:
        raise ValueError("Routing index hash does not match managed-files manifest")
    return managed_hashes


def _decode_routing_payload(vault: Path, payload: dict[str, Any]) -> tuple[list[SkillRecord], list[GraphEdge]]:
    schema = str(payload["schema_version"])
    raw_skills = payload.get("skills")
    raw_edges = payload.get("edges", [])
    if not isinstance(raw_skills, list) or not isinstance(raw_edges, list):
        raise ValueError("Routing index skills and edges must be lists")
    records: list[SkillRecord] = []
    ids: set[str] = set()
    for position, item in enumerate(raw_skills):
        if not isinstance(item, dict):
            raise ValueError(f"Malformed routing skill at position {position}: expected object")
        if schema == "0.2" and set(item) - SKILL_FIELDS:
            unknown = ", ".join(sorted(set(item) - SKILL_FIELDS))
            raise ValueError(f"Unknown routing skill fields at position {position}: {unknown}")
        record = _decode_skill(vault, item, position)
        if record.id in ids:
            raise ValueError(f"Duplicate routing skill id: {record.id}")
        ids.add(record.id)
        records.append(record)
    edges = [_decode_edge(item, position, ids, schema) for position, item in enumerate(raw_edges)]
    return records, edges


def _decode_skill(vault: Path, item: dict[str, Any], position: int) -> SkillRecord:
    skill_id = _required_text(item, "id", f"skill {position}")
    name = _required_text(item, "name", f"skill {position}")
    if not ROUTING_ID_RE.fullmatch(skill_id):
        raise ValueError(f"Unsafe routing skill id: {skill_id!r}")
    package = _text(item, "package", "", f"skill {skill_id}")
    if package and (len(package) > 64 or not ROUTING_PACKAGE_RE.fullmatch(package)):
        raise ValueError(f"Unsafe routing package for {skill_id}: {package!r}")
    card_path = _required_text(item, "card_path", f"skill {skill_id}")
    expected_card = (
        f"01_Packages/{package}/skills/{skill_id}.md"
        if package else f"04_Inbox/unclassified/{skill_id}.md"
    )
    if card_path.replace("\\", "/") != expected_card:
        raise ValueError(f"Routing card path does not match id/package for {skill_id}: {card_path!r}")
    card = _safe_registry_vault_path(vault.resolve(), card_path, allowed_roots={"01_Packages", "04_Inbox"})
    if not card.is_file():
        raise ValueError(f"Routing skill card is missing for {skill_id}: {card}")

    route_mode = _enum_text(item, "route_mode", "default", ROUTE_MODE_VALUES, f"skill {skill_id}")
    trigger_mode = _enum_text(
        item, "trigger_mode", "direct-or-semantic-match", TRIGGER_MODE_VALUES, f"skill {skill_id}"
    )
    risk_level = _enum_text(item, "risk_level", "unknown", RISK_VALUES, f"skill {skill_id}")
    topic_risk = _enum_text(item, "topic_risk", "unknown", TOPIC_RISK_VALUES, f"skill {skill_id}")
    action_risk = _enum_text(item, "action_risk", "unknown", RISK_VALUES, f"skill {skill_id}")
    review_state = _enum_text(item, "review_state", "confirmed", REVIEW_STATES, f"skill {skill_id}")
    confidence = _finite_score(item.get("confidence", 0.0), f"skill {skill_id} confidence")
    source_relative_path = _text(item, "source_relative_path", "", f"skill {skill_id}")
    if source_relative_path:
        _safe_registry_relative(source_relative_path, allow_dot=True)
    provenance = item.get("provenance", {})
    if not isinstance(provenance, dict):
        raise ValueError(f"Routing provenance for {skill_id} must be an object")
    actions = _string_list(item, "actions", skill_id)
    side_effects = _string_list(item, "side_effects", skill_id)
    permissions = _string_list(item, "permissions", skill_id)
    network_access = _optional_bool(item, "network_access", skill_id)
    writes_files = _optional_bool(item, "writes_files", skill_id)
    inferred_action = _inferred_registry_action_risk(actions, side_effects, permissions, network_access, writes_files)
    if RISK_RANK[action_risk] < RISK_RANK[inferred_action] or RISK_RANK[risk_level] < max(
        RISK_RANK[action_risk], RISK_RANK[inferred_action]
    ):
        raise ValueError(f"Routing risk fields are inconsistent with actions/permissions for {skill_id}")

    return SkillRecord(
        id=skill_id, name=name, source_path=card_path, detected_files=[],
        description=_text(item, "description", "", f"skill {skill_id}"),
        when_to_use=_text(item, "when_to_use", "", f"skill {skill_id}"),
        input_types=_string_list(item, "input_types", skill_id),
        output_types=_string_list(item, "output_types", skill_id),
        risk_level=risk_level, package=package, confidence=confidence,
        alternate_packages=_string_list(item, "alternate_packages", skill_id),
        source_root_id=_text(item, "source_root_id", "", f"skill {skill_id}"),
        source_relative_path=source_relative_path,
        source_hash=_text(item, "source_hash", "", f"skill {skill_id}"),
        source_mtime=_text(item, "source_mtime", "", f"skill {skill_id}"),
        last_scanned=_text(item, "last_scanned", "", f"skill {skill_id}"),
        provider=_text(item, "provider", "user", f"skill {skill_id}"),
        compatibility=_text(item, "compatibility", "", f"skill {skill_id}"),
        allowed_tools=_string_list(item, "allowed_tools", skill_id),
        aliases=_string_list(item, "aliases", skill_id),
        positive_triggers=_string_list(item, "positive_triggers", skill_id),
        negative_triggers=_string_list(item, "negative_triggers", skill_id),
        actions=actions,
        artifacts=_string_list(item, "artifacts", skill_id),
        requires=_string_list(item, "requires", skill_id),
        conflicts=_string_list(item, "conflicts", skill_id),
        workflow_next=_string_list(item, "workflow_next", skill_id),
        related_candidates=_string_list(item, "related_candidates", skill_id),
        workflow_candidates=_string_list(item, "workflow_candidates", skill_id),
        duplicate_candidates=_string_list(item, "duplicate_candidates", skill_id),
        conflict_candidates=_string_list(item, "conflict_candidates", skill_id),
        route_mode=route_mode, trigger_mode=trigger_mode,
        default_routable=_strict_bool(item, "default_routable", True, skill_id),
        requires_explicit_user_intent=_strict_bool(item, "requires_explicit_user_intent", False, skill_id),
        topic_risk=topic_risk, action_risk=action_risk,
        side_effects=side_effects,
        permissions=permissions,
        permissions_known=_strict_bool(item, "permissions_known", False, skill_id),
        network_access=network_access,
        writes_files=writes_files,
        status=_text(item, "status", "unknown", f"skill {skill_id}"),
        review_state=review_state, provenance=dict(provenance),
    )


def _decode_edge(item: Any, position: int, ids: set[str], schema: str) -> GraphEdge:
    if not isinstance(item, dict):
        raise ValueError(f"Malformed routing edge at position {position}: expected object")
    if schema == "0.2" and set(item) - EDGE_FIELDS:
        unknown = ", ".join(sorted(set(item) - EDGE_FIELDS))
        raise ValueError(f"Unknown routing edge fields at position {position}: {unknown}")
    source = _required_text(item, "source", f"edge {position}")
    target = _required_text(item, "target", f"edge {position}")
    relation = _required_text(item, "relation", f"edge {position}")
    if relation not in GRAPH_RELATIONS:
        raise ValueError(f"Invalid routing edge relation: {relation}")
    if not _valid_edge_endpoint(source, ids) or not _valid_edge_endpoint(target, ids):
        raise ValueError(f"Dangling or unsafe routing edge: {source} -> {target}")
    review_state = _enum_text(item, "review_state", "confirmed", REVIEW_STATES, f"edge {source}->{target}")
    origin = _text(item, "origin", "legacy", f"edge {source}->{target}")
    if schema == "0.2" and review_state == "confirmed" and origin in CANDIDATE_EDGE_ORIGINS:
        raise ValueError(f"Candidate routing edge cannot be confirmed: {source} -> {target}")
    return GraphEdge(
        source, target, relation,
        _text(item, "reason", "", f"edge {source}->{target}"),
        _finite_score(item.get("confidence", 1.0), f"edge {source}->{target} confidence"),
        origin, review_state,
        _text(item, "evidence", "", f"edge {source}->{target}"),
        _text(item, "rule_version", schema, f"edge {source}->{target}"),
    )


def _text(item: dict[str, Any], key: str, default: str, context: str) -> str:
    value = item.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"Routing field {key} for {context} must be a string")
    return value


def _required_text(item: dict[str, Any], key: str, context: str) -> str:
    value = _text(item, key, "", context).strip()
    if not value:
        raise ValueError(f"Routing field {key} for {context} is required")
    return value


def _enum_text(item: dict[str, Any], key: str, default: str, values: set[str], context: str) -> str:
    value = _text(item, key, default, context)
    if value not in values:
        raise ValueError(f"Unknown routing field {key} for {context}: {value!r}")
    return value


def _string_list(item: dict[str, Any], key: str, skill_id: str) -> list[str]:
    value = item.get(key, [])
    if not isinstance(value, list) or any(not isinstance(element, str) for element in value):
        raise ValueError(f"Routing field {key} for skill {skill_id} must be a list of strings")
    return list(value)


def _strict_bool(item: dict[str, Any], key: str, default: bool, skill_id: str) -> bool:
    value = item.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"Routing field {key} for skill {skill_id} must be boolean")
    return value


def _optional_bool(item: dict[str, Any], key: str, skill_id: str) -> bool | None:
    value = item.get(key)
    if value is not None and not isinstance(value, bool):
        raise ValueError(f"Routing field {key} for skill {skill_id} must be boolean or null")
    return value


def _finite_score(value: Any, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{context} must be numeric")
    score = float(value)
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        raise ValueError(f"{context} must be finite and between 0 and 1")
    return score


def _inferred_registry_action_risk(
    actions: list[str],
    side_effects: list[str],
    permissions: list[str],
    network_access: bool | None,
    writes_files: bool | None,
) -> str:
    level, _ = infer_action_risk(" ".join([*actions, *side_effects, *permissions]))
    if (network_access is True or writes_files is True) and RISK_RANK[level] < RISK_RANK["medium"]:
        return "medium"
    return level


def _valid_edge_endpoint(value: str, ids: set[str]) -> bool:
    if value in ids:
        return True
    prefix, separator, name = value.partition(":")
    return bool(separator and prefix in {"package", "inbox"} and ROUTING_PACKAGE_RE.fullmatch(name))


def _safe_registry_relative(value: str, *, allow_dot: bool = False) -> Path:
    normalized = value.replace("\\", "/")
    if allow_dot and normalized in {"", "."}:
        return Path()
    if not normalized or normalized.startswith(("/", "//")) or re.match(r"^[A-Za-z]:", normalized):
        raise ValueError(f"Routing path must be relative: {value!r}")
    pure = PurePosixPath(normalized)
    if any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError(f"Routing path traversal is not allowed: {value!r}")
    return Path(*pure.parts)


def _safe_registry_vault_path(vault: Path, value: str, *, allowed_roots: set[str] | None = None) -> Path:
    relative = _safe_registry_relative(value)
    if allowed_roots is not None and relative.parts[0] not in allowed_roots:
        raise ValueError(f"Routing path root is not allowed: {value!r}")
    current = vault.resolve()
    for part in relative.parts:
        current = current / part
        if _routing_linklike(current):
            raise ValueError(f"Routing path traverses a symlink or junction: {current}")
    if not current.resolve(strict=False).is_relative_to(vault.resolve()):
        raise ValueError(f"Routing path escapes Vault: {value!r}")
    return current


def _routing_linklike(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def route_vault(
    vault: Path,
    query: str,
    *,
    explain: bool = False,
    strategy: str = "legacy",
    profile: str = "",
) -> RouteDecision:
    payload, _, managed_hashes = _read_routing_payload(vault)
    records, edges = _decode_routing_payload(vault, payload)
    schema = str(payload.get("schema_version", "0.1"))
    effective_strategy = "legacy" if schema == "0.1" else strategy
    warnings = ("routing-index 0.1 is deprecated; rescan to generate 0.2",) if schema == "0.1" else ()
    decision = route_records(
        query,
        records,
        explain=explain,
        strategy=effective_strategy,
        profile=profile,
        edges=edges,
        warnings=warnings,
    )
    if schema == "0.2":
        _verify_decision_cards(vault.resolve(), decision, records, managed_hashes, explain=explain)
    return decision


def _verify_decision_cards(
    vault: Path,
    decision: RouteDecision,
    records: list[SkillRecord],
    managed_hashes: dict[str, str],
    *,
    explain: bool,
) -> None:
    selected_ids: set[str] = set()
    if decision.primary:
        selected_ids.add(decision.primary.id)
    if explain:
        selected_ids.update(record.id for _, record in decision.alternatives)
    for step in decision.plan:
        skill = step.get("skill")
        if isinstance(skill, dict) and isinstance(skill.get("id"), str):
            selected_ids.add(skill["id"])
    by_id = {record.id: record for record in records}
    for skill_id in selected_ids:
        record = by_id.get(skill_id)
        if record is None:
            raise ValueError(f"Route plan references unknown skill card: {skill_id}")
        relative = _card_path(record)
        expected = managed_hashes.get(relative)
        if expected is None:
            raise ValueError(f"Managed-files manifest is missing routed skill card: {relative}")
        card = _safe_registry_vault_path(vault, relative, allowed_roots={"01_Packages", "04_Inbox"})
        try:
            size = card.stat().st_size
            if size > 4 * 1024 * 1024:
                raise ValueError(f"Routed skill card exceeds 4194304 bytes: {relative}")
            actual = hashlib.sha256(card.read_bytes()).hexdigest()
        except OSError as exc:
            raise ValueError(f"Cannot verify routed skill card {relative}: {exc}") from exc
        if actual != expected:
            raise ValueError(f"Routed skill card hash does not match managed-files manifest: {relative}")


def query_context(query: str) -> tuple[set[str], dict[str, tuple[str, ...]], bool]:
    lowered = query.lower()
    query_terms = set(terms(query))
    evidence: dict[str, list[str]] = defaultdict(list)
    for needle, phrase, packages, extra_terms in CHINESE_QUERY_SIGNALS:
        lowered_needle = needle.lower()
        if lowered_needle not in lowered or is_negated_signal(lowered, lowered_needle):
            continue
        query_terms.update(terms(phrase))
        query_terms.update(extra_terms)
        for package in packages:
            evidence[package].append(f"{needle}->{phrase}")
    for needle, extra_terms in ACTION_TERM_SIGNALS:
        if needle in lowered and not is_negated_signal(lowered, needle):
            query_terms.update(extra_terms)
    return query_terms, {package: tuple(values) for package, values in evidence.items()}, has_constraint(query)


def has_constraint(query: str) -> bool:
    lowered = query.lower()
    return any(phrase in lowered for phrase in CONSTRAINT_PHRASES)


def is_negated_signal(lowered: str, needle: str) -> bool:
    index = lowered.find(needle)
    if index < 0:
        return False
    before = lowered[max(0, index - 10):index]
    return any(marker in before for marker in NEGATION_PHRASES)


def terms(value: str) -> set[str]:
    return set(search_tokens(value))


def _package_ambiguous(result: RankedResult) -> bool:
    if len(result.package_candidates) < 2:
        return False
    first, second = result.package_candidates[:2]
    if result.strategy in {"hybrid", "hybrid-graph"}:
        return second.score >= 1.8 and (first.score - second.score) <= 0.12
    return second.score >= 2.0 and (first.score - second.score) <= 1.0


def _skill_ambiguous(result: RankedResult) -> bool:
    if len(result.scored_records) < 2:
        return False
    first_score, first = result.scored_records[0]
    second_score, second = result.scored_records[1]
    if result.strategy in {"hybrid", "hybrid-graph"}:
        return first.package == second.package and first_score >= 1.8 and (first_score - second_score) <= 0.12
    return first.package == second.package and first_score >= 2.0 and (first_score - second_score) <= 0.5


def _related_packages(result: RankedResult, primary_package: str) -> list[str]:
    if result.constraint_detected or not result.sequential_task:
        return []
    return [candidate.package for candidate in result.package_candidates[1:3] if candidate.package != primary_package]


def _underspecified_intent(query: str, result: RankedResult) -> bool:
    lowered = query.lower()
    # A broad request is underspecified even when the registry happens to have
    # only one vaguely related skill.  Candidate count is an index accident,
    # not positive evidence that the user's intended artifact is known.
    return bool(result.scored_records) and any(phrase in lowered for phrase in AMBIGUOUS_INTENT_PHRASES)


def _requires_review(record: SkillRecord) -> bool:
    return (
        record.risk_level in {"high", "unknown"}
        or record.action_risk == "high"
        or not record.default_routable
        or record.route_mode in {"manual", "explicit-only"}
        or record.trigger_mode in {"manual", "explicit-only"}
    )


def _explicit_skill_request(query: str) -> bool:
    normalized = normalize(query)
    english = re.match(
        r"^(?:please\s+)?(?:use|invoke|load)\s+"
        r"(?!a\b|an\b|the\b|any\b|this\b|that\b|these\b|those\b|my\b|our\b|your\b|its\b)"
        r"(?:skill\s+)?"
        r"[a-z0-9_.+#-]+(?:\s+[a-z0-9_.+#-]+){0,3}(?:\s+(?:to|for)\b|$)",
        normalized,
    )
    if english:
        return True
    return bool(re.match(r"^(?:请)?(?:使用|调用|加载)\s*[^\s，。；]+", normalized))


def _is_unsupported_task(query: str) -> bool:
    lowered = query.lower()
    if any(phrase in lowered for phrase in ("delete all", "\u5220\u9664\u6240\u6709", "complete payment", "\u5b8c\u6210\u4ed8\u6b3e")):
        return True
    has_secret = any(phrase in lowered for phrase in ("token", "secret", "\u51ed\u8bc1"))
    sends_external = any(phrase in lowered for phrase in ("external api", "send to", "\u53d1\u7ed9\u5916\u90e8", "\u53d1\u7ed9"))
    if has_secret and sends_external:
        return True
    runs_unknown = any(phrase in lowered for phrase in ("unknown script", "\u672a\u77e5\u811a\u672c")) and any(
        phrase in lowered for phrase in ("run", "execute", "\u8fd0\u884c", "\u6267\u884c")
    )
    return runs_unknown


def _requires_named_intent(record: SkillRecord) -> bool:
    text = f"{record.description} {record.when_to_use}".lower()
    return "trigger only when" in text and "explicit" in text


def _has_named_intent(record: SkillRecord, query: str) -> bool:
    generic = {"skill", "guide", "tool", "workflow", "review", "analysis"}
    name_terms = terms(record.name) - generic
    lowered = query.lower()
    return bool(name_terms & terms(lowered)) or record.name.lower() in lowered


def _card_path(record: SkillRecord) -> str:
    if not record.package:
        return f"04_Inbox/unclassified/{record.id}.md"
    return f"01_Packages/{record.package}/skills/{record.id}.md"


def _candidate_payload(record: SkillRecord | None, score: float) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "id": record.id,
        "name": record.name,
        "package": record.package,
        "when_to_use": record.when_to_use,
        "risk_level": record.risk_level,
        "card_path": _card_path(record),
        "score": round(score, 2),
    }


def _estimate_tokens(value: str) -> int:
    return max(1, (len(value) + 3) // 4)


def _clarification(decision: RouteDecision) -> dict[str, Any]:
    candidates = [record for _, record in decision.alternatives[:2]]
    if len(candidates) < 2:
        return {"question": "Which outcome do you want from this task?", "options": []}
    left, right = candidates
    left_goal = (left.output_types or left.artifacts or left.actions or [left.name])[0]
    right_goal = (right.output_types or right.artifacts or right.actions or [right.name])[0]
    return {
        "question": f"Do you want {left_goal} ({left.name}) or {right_goal} ({right.name})?",
        "options": [left.name, right.name],
    }


def _split_task(query: str) -> list[str]:
    parts = re.split(r"(?:\bthen\b|\bafter that\b|\bbefore\b|\bnext\b|然后|之后|再)", query, flags=re.IGNORECASE)
    cleaned = [part.strip(" ,，。;；") for part in parts if part.strip(" ,，。;；")]
    return cleaned if len(cleaned) > 1 else [query]


def _split_task_v2(query: str) -> list[str]:
    # "A after B" describes execution order B, A.  "A after that B" is a
    # forward discourse marker and keeps surface order.  Handle the simple
    # binary form before the general splitter so the planner does not silently
    # invert the user's requested dependency.
    bare_after = re.search(r"\bafter\b(?!\s+that\b)", query, flags=re.IGNORECASE)
    other_sequence = re.search(
        r"(?:\bthen\b|\band save\b|\band then\b|\bafter that\b|"
        r"\bbefore\b(?!\s+(?:i|we|you)\b)|\bnext\b|然后|之后|"
        r"再(?!现|生|次|版|度)|并把|并将|整理成)",
        query,
        flags=re.IGNORECASE,
    )
    if bare_after and not other_sequence:
        left = query[:bare_after.start()].strip(" ,，。；;")
        right = query[bare_after.end():].strip(" ,，。；;")
        if left and right:
            return [right, left]
    parts = re.split(
        r"(?:\bthen\b|\band save\b|\band then\b|\bafter that\b|\bafter\b|"
        r"\bbefore\b(?!\s+(?:i|we|you)\b)|\bnext\b|然后|之后|"
        r"再(?!现|生|次|版|度)|并把|并将|整理成)",
        query,
        flags=re.IGNORECASE,
    )
    cleaned = [part.strip(" ,，。；;") for part in parts if part.strip(" ,，。；;")]
    return cleaned if len(cleaned) > 1 else [query]


def _minimum_cost_cover(candidate_sets: list[list[tuple[float, SkillRecord]]]) -> set[str]:
    skills: dict[str, SkillRecord] = {}
    coverage: dict[str, int] = defaultdict(int)
    relevance_cost: dict[str, float] = defaultdict(float)
    for step, candidates in enumerate(candidate_sets):
        top_score = candidates[0][0]
        for rank, (score, skill) in enumerate(candidates):
            if score < top_score - 0.12:
                continue
            skills[skill.id] = skill
            coverage[skill.id] |= 1 << step
            relevance_cost[skill.id] += rank * 0.05 + max(0.0, top_score - score)
    target = (1 << len(candidate_sets)) - 1
    best: dict[int, tuple[float, tuple[str, ...]]] = {0: (0.0, ())}
    for skill_id in sorted(skills):
        skill = skills[skill_id]
        risk_cost = 0.3 if skill.risk_level == "high" else 0.1 if skill.risk_level == "medium" else 0.0
        item_cost = 1.0 + risk_cost + relevance_cost[skill_id]
        for mask, (cost, chosen) in list(best.items()):
            new_mask = mask | coverage[skill_id]
            proposal = (cost + item_cost, (*chosen, skill_id))
            current = best.get(new_mask)
            if current is None or proposal < current:
                best[new_mask] = proposal
    return set(best.get(target, (0.0, tuple(candidates[0][1].id for candidates in candidate_sets)))[1])


def _greedy_cover(candidate_sets: list[list[tuple[float, SkillRecord]]]) -> set[str]:
    uncovered = set(range(len(candidate_sets)))
    chosen: set[str] = set()
    while uncovered:
        options: dict[str, tuple[SkillRecord, set[int], float]] = {}
        for step in uncovered:
            top_score = candidate_sets[step][0][0]
            for rank, (score, skill) in enumerate(candidate_sets[step]):
                if score < top_score - 0.12:
                    continue
                current = options.get(skill.id, (skill, set(), 0.0))
                options[skill.id] = (skill, {*current[1], step}, current[2] + rank * 0.05 + top_score - score)
        skill_id, (_, covered, _) = min(
            options.items(), key=lambda item: ((1.0 + item[1][2]) / len(item[1][1]), item[0])
        )
        chosen.add(skill_id)
        uncovered -= covered
    return chosen


def _expand_required_skills(
    selected: list[SkillRecord],
    records: list[SkillRecord],
    edges: list[GraphEdge],
    *,
    query: str,
    profile: str,
) -> tuple[list[SkillRecord], str, bool]:
    by_id = {record.id: record for record in records}
    by_reference: dict[str, list[SkillRecord]] = defaultdict(list)
    by_normalized_id = {normalize(record.id): record for record in records}
    for record in records:
        for value in (record.name, *record.aliases):
            if value:
                by_reference[normalize(value)].append(record)
    required: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        if edge.review_state == "confirmed" and edge.relation == "requires":
            required[edge.source].append(edge.target)
    # Preserve explicit metadata even when graph construction could not resolve
    # a target.  Dangling dependencies must fail closed instead of disappearing.
    for record in records:
        for reference in record.requires:
            normalized = normalize(reference)
            target = by_normalized_id.get(normalized)
            if target is not None:
                required[record.id].append(target.id)
                continue
            matches = {item.id: item for item in by_reference.get(normalized, [])}
            if len(matches) == 1:
                required[record.id].append(next(iter(matches)))
            elif len(matches) > 1:
                required[record.id].append(f"ambiguous:{reference}")
            else:
                required[record.id].append(f"missing:{reference}")
    expanded = {record.id: record for record in selected}
    queue = list(expanded)
    policy_review = False
    while queue:
        source = queue.pop(0)
        for target in dict.fromkeys(required.get(source, [])):
            if target.startswith("ambiguous:"):
                return (
                    list(expanded.values()),
                    f"{source} has ambiguous required skill {target[10:]}; use a stable skill id",
                    policy_review,
                )
            if target.startswith("missing:"):
                return list(expanded.values()), f"{source} requires missing skill {target[8:]}", policy_review
            dependency = by_id.get(target)
            if dependency is None:
                return list(expanded.values()), f"{source} requires missing skill {target}", policy_review
            if target not in expanded:
                reason = policy_exclusions(query, [dependency], profile=profile).get(target, "")
                if reason in {"compatibility does not match routing profile", "negative trigger matched"}:
                    return list(expanded.values()), f"{source} requires {target}, but {reason}", policy_review
                if reason:
                    policy_review = True
                expanded[target] = dependency
                queue.append(target)
    return list(expanded.values()), "", policy_review


def _order_plan(records: list[SkillRecord], edges: list[GraphEdge]) -> tuple[list[SkillRecord], bool]:
    by_id = {record.id: record for record in records}
    position = {record.id: index for index, record in enumerate(records)}
    indegree = {record.id: 0 for record in records}
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        if edge.review_state != "confirmed" or edge.relation not in {"workflow_next", "requires"}:
            continue
        source, target = edge.source, edge.target
        if source not in by_id or target not in by_id:
            continue
        if edge.relation == "requires":
            source, target = target, source
        outgoing[source].append(target)
        indegree[target] += 1
    queue = sorted((node for node, degree in indegree.items() if degree == 0), key=lambda node: position[node])
    ordered: list[SkillRecord] = []
    while queue:
        node = queue.pop(0)
        ordered.append(by_id[node])
        for target in sorted(outgoing[node], key=lambda item: position[item]):
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
                queue.sort(key=lambda item: position[item])
    return ordered, len(ordered) != len(records)


def _apply_graph_lift(
    result: RankedResult,
    edges: list[GraphEdge],
    all_records: list[SkillRecord] | None = None,
) -> RankedResult:
    if not result.scored_records:
        return result
    candidates = result.scored_records[:20]
    lexical_ids = {record.id for _, record in candidates}
    all_ids = {
        record.id for record in (all_records or [record for _, record in result.scored_records])
    }
    ids = set(lexical_ids)
    usable_edges = [
        edge for edge in edges
        if edge.review_state == "confirmed" and edge.relation in {"workflow_next", "requires", "governs"}
    ]
    if not usable_edges:
        return result
    for edge in usable_edges:
        if edge.review_state != "confirmed" or edge.relation not in {"workflow_next", "requires", "governs"}:
            continue
        if edge.source in lexical_ids and edge.target in all_ids:
            ids.add(edge.target)
        if edge.target in lexical_ids and edge.source in all_ids:
            ids.add(edge.source)
    seed = {record.id: max(score, 0.0) for score, record in candidates}
    total = sum(seed.values()) or 1.0
    seed = {key: value / total for key, value in seed.items()}
    adjacency: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for edge in usable_edges:
        if edge.source not in ids or edge.target not in ids:
            continue
        adjacency[edge.source].append((edge.target, max(0.0, edge.confidence)))
    if not adjacency:
        return result
    rank = dict(seed)
    for _ in range(3):
        next_rank = {node: 0.8 * seed.get(node, 0.0) for node in ids}
        for source, value in rank.items():
            targets = adjacency.get(source, [])
            weight_sum = sum(weight for _, weight in targets) or 1.0
            for target, weight in targets:
                next_rank[target] += 0.2 * value * weight / weight_sum
        rank = next_rank
    baseline = {node: 0.8 * seed.get(node, 0.0) for node in ids}
    lifted = [
        (
            score * (1.0 + min(0.08, max(0.0, rank.get(record.id, 0.0) - baseline.get(record.id, 0.0)))),
            record,
        )
        for score, record in result.scored_records
    ]
    lifted.sort(key=lambda item: (-item[0], item[1].name.lower(), item[1].id))
    return RankedResult(
        records=[record for _, record in lifted], scored_records=lifted,
        package_candidates=result.package_candidates, package_evidence=result.package_evidence,
        normalized_terms=result.normalized_terms, constraint_detected=result.constraint_detected,
        sequential_task=result.sequential_task, strategy=result.strategy, evidence=result.evidence,
        excluded_candidates=result.excluded_candidates,
    )


def apply_graph_shadow(
    result: RankedResult,
    edges: list[GraphEdge],
    all_records: list[SkillRecord] | None = None,
) -> RankedResult:
    """Apply bounded confirmed-edge reranking for evaluation and shadow routing."""
    return result if result.constraint_detected else _apply_graph_lift(result, edges, all_records)
