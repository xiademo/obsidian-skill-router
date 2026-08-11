from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import json
import math
import re
from pathlib import Path
from typing import Any

from .graph import build_skill_edges
from .models import SkillRecord
from .routing import PackageCandidate, RankedResult, apply_graph_shadow, load_routing_index, rank_records, route_records
from .sanitize import markdown_plain
from .scan import scan_roots


@dataclass(frozen=True)
class EvalCase:
    query: str
    expected_package: str = ""
    expected_skill: str = ""
    id: str = ""
    category: str = "general"
    expected_related_packages: tuple[str, ...] = ()
    expected_top3_contains: tuple[str, ...] = ()
    should_trigger: bool = True
    allow_governance_review: bool = False
    risk_expectation: str = ""
    forbid_graph_expansion: bool = False
    ambiguous: bool = False
    expected_top2_packages: tuple[str, ...] = ()
    expected_decision: str = ""
    acceptable_primary_skills: tuple[str, ...] = ()
    importance: int = 1
    notes: str = ""
    intent_family: str = ""
    profile: str = ""
    required_plan_skills: tuple[str, ...] = ()
    forbidden_skills: tuple[str, ...] = ()
    required_order: tuple[tuple[str, str], ...] = ()
    expected_review: bool | None = None


DECISION_TYPES = frozenset({
    "recommend",
    "clarify_required",
    "continue_without_skill",
    "review_required",
    "skill_plan",
})
ACTIVE_DECISION_TYPES = frozenset({"recommend", "review_required", "skill_plan"})
RISK_EXPECTATIONS = frozenset({"", "unknown", "low", "medium", "high"})
EVAL_CASE_FIELDS = frozenset({
    "acceptable_primary_skills",
    "allow_governance_review",
    "ambiguous",
    "category",
    "expected_decision",
    "expected_package",
    "expected_primary_package",
    "expected_related_packages",
    "expected_review",
    "expected_skill",
    "expected_top_skill",
    "expected_top2_packages",
    "expected_top3_contains",
    "forbid_graph_expansion",
    "forbidden_skills",
    "id",
    "importance",
    "intent_family",
    "notes",
    "profile",
    "query",
    "required_order",
    "required_plan_skills",
    "risk_expectation",
    "should_trigger",
    "user_task",
})
EVAL_BOOL_FIELDS = frozenset({
    "allow_governance_review",
    "ambiguous",
    "expected_review",
    "forbid_graph_expansion",
    "should_trigger",
})
EVAL_LIST_FIELDS = frozenset({
    "acceptable_primary_skills",
    "expected_related_packages",
    "expected_top2_packages",
    "expected_top3_contains",
    "forbidden_skills",
    "required_plan_skills",
})
MAX_EVAL_FILE_BYTES = 16 * 1024 * 1024


CHINESE_QUERY_SIGNALS: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("简历", "resume", ("career-resume",), ("resume", "career")),
    ("jd", "job description", ("career-resume",), ("job", "resume")),
    ("岗位", "job role", ("career-resume",), ("job", "role")),
    ("面试", "interview", ("career-resume",), ("interview", "resume")),
    ("求职", "career", ("career-resume",), ("career", "resume")),
    ("pdf", "pdf", ("document-processing",), ("pdf",)),
    ("word", "word docx", ("document-processing",), ("word", "docx")),
    ("文档", "document", ("document-processing",), ("document", "doc")),
    ("文件", "file document", ("document-processing",), ("file", "document")),
    ("报告", "report", ("research-writing", "document-processing"), ("report",)),
    ("导出", "export", ("document-processing",), ("export",)),
    ("页码", "page", ("document-processing",), ("page", "pdf")),
    ("ocr", "ocr", ("document-processing",), ("ocr", "pdf")),
    ("识别", "ocr extract", ("document-processing",), ("ocr", "extract")),
    ("复制文字", "selectable text", ("document-processing",), ("text", "extract")),
    ("代码", "code", ("coding-workflow",), ("code",)),
    ("项目", "repo project", ("coding-workflow",), ("repo", "project")),
    ("测试", "tests", ("coding-workflow",), ("tests", "test")),
    ("重构", "refactor", ("coding-workflow",), ("refactor", "code")),
    ("debug", "debug", ("coding-workflow",), ("debug", "code")),
    ("git", "git", ("coding-workflow",), ("git", "repo")),
    ("调研", "research", ("research-writing",), ("research",)),
    ("论文", "paper", ("research-writing",), ("paper", "research")),
    ("引用", "citation", ("research-writing",), ("citation", "research")),
    ("搜索", "search", ("research-writing",), ("search", "research")),
    ("资料", "material notes", ("research-writing", "document-processing"), ("material", "notes")),
    ("知识库", "knowledge base obsidian", ("memory-knowledge",), ("knowledge", "obsidian", "note")),
    ("obsidian", "obsidian", ("memory-knowledge",), ("obsidian", "note")),
    ("笔记", "note", ("memory-knowledge",), ("note",)),
    ("记忆", "memory", ("memory-knowledge",), ("memory", "knowledge")),
    ("归档", "archive", ("memory-knowledge",), ("archive", "note")),
    ("风险", "risk", ("safety-governance",), ("risk", "safety")),
    ("权限", "permission", ("safety-governance",), ("permission", "risk")),
    ("token", "token", ("safety-governance",), ("token", "secret")),
    ("secret", "secret", ("safety-governance",), ("secret",)),
    ("凭证", "credential", ("safety-governance",), ("credential", "secret")),
    ("注入", "injection", ("safety-governance",), ("injection", "risk")),
    ("自动化", "automation", ("automation-ops",), ("automation",)),
    ("邮件", "email", ("automation-ops",), ("email",)),
    ("浏览器", "browser", ("automation-ops",), ("browser",)),
    ("批处理", "batch automation", ("automation-ops",), ("batch", "automation")),
    ("工具", "tool index", ("automation-ops", "memory-knowledge"), ("tool", "index")),
    ("整理", "organize", ("document-processing", "memory-knowledge"), ("organize",)),
    ("看看", "review", ("coding-workflow", "safety-governance"), ("review",)),
    ("有没有问题", "review risk issue", ("coding-workflow", "safety-governance"), ("review", "risk")),
    ("分类", "classify skill risk", ("safety-governance",), ("classify", "skill", "risk")),
    ("skill", "skill", ("safety-governance",), ("skill",)),
)

CONSTRAINT_PHRASES = (
    "只",
    "不要",
    "不需要",
    "无需",
    "仅",
    "不是",
    "only",
    "do not",
    "don't",
    "not ",
    "no workflow",
    "no follow-up",
    "no follow up",
)


def run_eval(
    roots: list[Path],
    cases_path: Path,
    package_rules: dict[str, list[str]] | None = None,
    risk_keywords: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    records, warnings = scan_roots(roots, package_rules=package_rules, risk_keywords=risk_keywords)
    return run_eval_on_records(records, warnings, cases_path)


def run_eval_on_records(records: list[SkillRecord], warnings: list[str], cases_path: Path, *, strategy: str = "legacy") -> dict[str, Any]:
    cases = load_eval_cases(cases_path)
    skill_edges = build_skill_edges(records)
    ranked_cases = [
        (_ranked_result(case.query, records, strategy=strategy, edges=skill_edges, profile=case.profile), case)
        for case in cases
    ]
    route_statuses = {
        case.id: route_records(case.query, records, strategy=strategy, edges=skill_edges, profile=case.profile).status
        for _, case in ranked_cases
    }

    package_total = sum(1 for _, case in ranked_cases if case.expected_package and case.should_trigger)
    skill_total = sum(1 for _, case in ranked_cases if case.expected_skill and case.should_trigger)
    negative_total = sum(1 for _, case in ranked_cases if not case.should_trigger)
    cross_total = sum(1 for _, case in ranked_cases if case.expected_related_packages and case.should_trigger)
    risk_total = sum(1 for _, case in ranked_cases if case.risk_expectation and case.should_trigger)
    ambiguous_total = sum(1 for _, case in ranked_cases if _expects_ambiguity(case))
    constraint_total = sum(1 for result, case in ranked_cases if result.constraint_detected or case.forbid_graph_expansion)

    package_hit = sum(1 for result, case in ranked_cases if _package_hit_at(result, case, 1))
    package_hit_2 = sum(1 for result, case in ranked_cases if _package_hit_at(result, case, 2))
    skill_hit_1 = sum(
        1
        for result, case in ranked_cases
        if case.expected_skill and case.should_trigger and result.records and _skill_matches(result.records[0], case.expected_skill)
    )
    skill_hit_3 = sum(
        1
        for result, case in ranked_cases
        if case.expected_skill and case.should_trigger and any(_skill_matches(record, case.expected_skill) for record in result.records[:3])
    )
    false_triggers = sum(1 for _, case in ranked_cases if not case.should_trigger and route_statuses[case.id] != "continue_without_skill")
    cross_hits = sum(1 for result, case in ranked_cases if _cross_package_hit(result, case))
    risk_hits = sum(
        1
        for result, case in ranked_cases
        if case.risk_expectation and case.should_trigger and result.records and result.records[0].risk_level == case.risk_expectation
    )
    graph_over_expansions = _graph_over_expansions(ranked_cases, skill_edges)
    ambiguity_hits = sum(
        1
        for result, case in ranked_cases
        if _expects_ambiguity(case) and _ambiguous_reasons(result)
    )
    overconfident = sum(1 for result, case in ranked_cases if _overconfident_top1(result, case))
    constraint_hits = sum(
        1
        for result, case in ranked_cases
        if (result.constraint_detected or case.forbid_graph_expansion) and not _graph_over_expanded(result, case, skill_edges)
    )
    failures, borderline = _case_results(ranked_cases, skill_edges, route_statuses)
    categories = Counter(case.category for _, case in ranked_cases)
    ranking_metrics = _ranking_quality(ranked_cases)
    calibration = _calibration_quality(ranked_cases)
    graph_diagnostics = (
        _graph_diagnostics(records, ranked_cases, skill_edges)
        if strategy == "hybrid-graph"
        else {
            "over_expansion_delta": None,
            "candidate_leakage_count": None,
            "lift_cap_violation_count": None,
        }
    )

    return {
        "cases": len(cases),
        "strategy": strategy,
        "skills": len(records),
        "warnings": warnings,
        "cases_by_category": dict(sorted(categories.items())),
        "cases_by_intent_family": dict(sorted(Counter(case.intent_family for case in cases if case.intent_family).items())),
        "metrics": {
            "package_hit_at_1": _rate(package_hit, package_total),
            "package_hit_at_2": _rate(package_hit_2, package_total),
            "skill_hit_at_1": _rate(skill_hit_1, skill_total),
            "skill_hit_at_3": _rate(skill_hit_3, skill_total),
            "false_trigger_rate": _rate(false_triggers, negative_total),
            "ambiguity_correctly_flagged": _rate(ambiguity_hits, ambiguous_total),
            "overconfident_top1_count": overconfident,
            "cross_package_hit": _rate(cross_hits, cross_total),
            "cross_package_related_hit": _rate(cross_hits, cross_total),
            "constraint_respect_rate": _rate(constraint_hits, constraint_total),
            "risk_expectation_match_rate": _rate(risk_hits, risk_total),
            "graph_over_expansion_count": graph_over_expansions,
            "mrr": ranking_metrics["mrr"],
            "ndcg_at_5": ranking_metrics["ndcg_at_5"],
            "brier_score": calibration["brier_score"],
            "ece": calibration["ece"],
            "macro_package_accuracy": _macro_package_accuracy(ranked_cases),
            "lexical_cross_package_recall": _rate(cross_hits, cross_total),
            "graph_incremental_top3_gain": _graph_incremental_gain(records, ranked_cases, skill_edges) if strategy == "hybrid-graph" else None,
            "graph_over_expansion_delta": graph_diagnostics["over_expansion_delta"],
            "graph_candidate_leakage_count": graph_diagnostics["candidate_leakage_count"],
            "graph_lift_cap_violation_count": graph_diagnostics["lift_cap_violation_count"],
        },
        "counts": {
            "package_cases": package_total,
            "skill_cases": skill_total,
            "negative_cases": negative_total,
            "cross_package_cases": cross_total,
            "risk_cases": risk_total,
            "ambiguous_cases": ambiguous_total,
            "constraint_cases": constraint_total,
            "package_hit_at_1": package_hit,
            "package_hit_at_2": package_hit_2,
            "skill_hit_at_1": skill_hit_1,
            "skill_hit_at_3": skill_hit_3,
            "false_triggers": false_triggers,
            "ambiguity_correctly_flagged": ambiguity_hits,
            "overconfident_top1": overconfident,
            "cross_package_hit": cross_hits,
            "constraint_respected": constraint_hits,
            "risk_expectation_matches": risk_hits,
            "graph_over_expansions": graph_over_expansions,
        },
        "failures": failures,
        "borderline_cases": borderline,
        "case_details": _case_details(ranked_cases, skill_edges),
        "top_failure_patterns": _failure_patterns(failures),
        "coverage_risk_curve": calibration["coverage_risk_curve"],
    }


def run_agent_first_eval_on_records(
    records: list[SkillRecord],
    warnings: list[str],
    cases_path: Path,
    audit_path: Path | None = None,
    *,
    strategy: str = "legacy",
) -> dict[str, Any]:
    red_team_cases = load_eval_cases(cases_path)
    audit_cases = load_eval_cases(audit_path) if audit_path else []
    combined_ids = [case.id.casefold() for case in [*red_team_cases, *audit_cases]]
    if len(combined_ids) != len(set(combined_ids)):
        raise ValueError("Eval case ids must be unique across cases and audit datasets")
    graph_edges = build_skill_edges(records)
    rows: list[dict[str, Any]] = []
    for source, cases in (("red_team", red_team_cases), ("manual_audit", audit_cases)):
        for case in cases:
            decision = route_records(
                case.query,
                records,
                explain=True,
                strategy=strategy,
                edges=graph_edges,
                profile=case.profile,
            ).to_dict()
            expected = _expected_route_status(case)
            actual_decision = _actual_decision_type(decision)
            primary = decision.get("primary") or {}
            acceptable = set(
                case.acceptable_primary_skills
                or ((case.expected_skill,) if case.expected_skill else ())
                or ((case.required_plan_skills[0],) if case.required_plan_skills else ())
            )
            expected_cross_packages = tuple(package for package in case.expected_related_packages if package != case.expected_package)
            primary_applicable = expected in ACTIVE_DECISION_TYPES
            primary_ok = bool(
                primary_applicable
                and primary
                and (
                    (not acceptable and primary.get("package") == case.expected_package)
                    or _skill_value_matches(str(primary.get("id", "")), str(primary.get("name", "")), acceptable)
                )
            )
            candidates = _decision_candidates(decision, limit=3)
            candidate_hits, candidate_required = _candidate_recall_counts(decision, case)
            recall_ok = candidate_required == 0 or candidate_hits == candidate_required
            candidate_recall = candidate_hits / candidate_required if candidate_required else None
            candidate_hit_at_3 = candidate_hits > 0 if candidate_required else None
            lexical_candidate_recall: float | None = None
            lexical_candidate_hit_at_3: bool | None = None
            if strategy == "hybrid-graph" and expected in ACTIVE_DECISION_TYPES:
                lexical = route_records(
                    case.query,
                    records,
                    explain=True,
                    strategy="hybrid",
                    edges=graph_edges,
                    profile=case.profile,
                ).to_dict()
                lexical_hits, lexical_required = _candidate_recall_counts(lexical, case)
                lexical_candidate_recall = lexical_hits / lexical_required if lexical_required else None
                lexical_candidate_hit_at_3 = lexical_hits > 0 if lexical_required else None
            plan = [step for step in decision.get("plan", []) if isinstance(step, dict)]
            plan_skills = [step.get("skill") for step in plan if isinstance(step.get("skill"), dict)]
            plan_hits = sum(
                1
                for required in case.required_plan_skills
                if any(_candidate_matches_expected(item, required) for item in plan_skills)
            )
            plan_actual_count = len(_unique_candidates(plan_skills))
            plan_recall = _rate(plan_hits, len(case.required_plan_skills))
            plan_precision_hits = sum(
                1
                for item in _unique_candidates(plan_skills)
                if any(_candidate_matches_expected(item, required) for required in case.required_plan_skills)
            )
            plan_precision = _rate(
                plan_precision_hits,
                plan_actual_count,
            ) if case.required_plan_skills else None
            plan_order_ok = _plan_order_ok(plan_skills, case.required_order) if case.required_order else True
            all_candidates = _decision_candidates(decision)
            forbidden_ok = not any(
                _candidate_matches_expected(item, forbidden)
                for item in all_candidates
                for forbidden in case.forbidden_skills
            )
            review_labeled = case.expected_review is not None or expected == "review_required"
            review_expected = case.expected_review if case.expected_review is not None else expected == "review_required"
            review_ok = (
                actual_decision == "review_required"
                if review_expected
                else actual_decision != "review_required"
            ) if review_labeled else True
            plan_packages = {
                str((step.get("skill") or {}).get("package", ""))
                for step in plan
                if isinstance(step, dict)
            }
            candidate_packages = {str(item.get("package", "")) for item in candidates if isinstance(item, dict)}
            routed_packages = set(decision.get("related_packages") or []) | plan_packages | candidate_packages
            legacy_constraint_ok = (
                not bool(decision.get("related_packages"))
                if (case.forbid_graph_expansion or decision.get("constraint_detected"))
                else True
            )
            rows.append(
                {
                    "source": source,
                    "id": case.id,
                    "intent_family": case.intent_family,
                    "profile": case.profile,
                    "category": case.category,
                    "query": case.query,
                    "expected_status": _legacy_status_for_decision(expected),
                    "actual_status": str(decision.get("status", "")),
                    "expected_decision": expected,
                    "actual_decision": actual_decision,
                    "decision_ok": actual_decision == expected,
                    "expected_package": case.expected_package,
                    "expected_skill": case.expected_skill,
                    "actual_skill": str(primary.get("id", "")),
                    "actual_skill_name": str(primary.get("name", "")),
                    "primary_ok": primary_ok,
                    "primary_applicable": primary_applicable,
                    "recall_ok": recall_ok,
                    "candidate_hits": candidate_hits,
                    "candidate_required": candidate_required,
                    "candidate_recall": candidate_recall,
                    "candidate_hit_at_3": candidate_hit_at_3,
                    "lexical_candidate_recall": lexical_candidate_recall,
                    "lexical_candidate_hit_at_3": lexical_candidate_hit_at_3,
                    "related_ok": bool(set(expected_cross_packages) & routed_packages) if expected_cross_packages and expected in ACTIVE_DECISION_TYPES else True,
                    "expects_cross_package": bool(expected_cross_packages),
                    "constraint_ok": legacy_constraint_ok and forbidden_ok,
                    "forbidden_ok": forbidden_ok,
                    "has_forbidden": bool(case.forbidden_skills),
                    "required_plan_skills": list(case.required_plan_skills),
                    "actual_plan_skills": [str(item.get("name") or item.get("id") or "") for item in plan_skills],
                    "plan_hits": plan_hits,
                    "plan_required": len(case.required_plan_skills),
                    "plan_actual": plan_actual_count,
                    "plan_precision_hits": plan_precision_hits,
                    "plan_recall": plan_recall,
                    "plan_precision": plan_precision,
                    "plan_order_ok": plan_order_ok,
                    "plan_order_required": bool(case.required_order),
                    "plan_review_labeled": bool(case.required_plan_skills) and review_labeled,
                    "plan_review_ok": review_ok,
                    "review_expected": review_expected if review_labeled else None,
                    "negative_case": not case.should_trigger,
                    "importance": max(1, case.importance),
                    "tokens": int((decision.get("budget") or {}).get("response_tokens_estimate", 0)),
                    "candidate_tokens": int((decision.get("budget") or {}).get("candidate_payload_tokens_estimate", 0)),
                    "reason": str(decision.get("reason", "")),
                }
            )
    result = _agent_first_summary(rows, warnings)
    result["strategy"] = strategy
    return result


def load_eval_cases(path: Path) -> list[EvalCase]:
    payload = _load_strict_json(path, "Eval cases")
    if isinstance(payload, dict):
        unknown_top = set(payload) - {"schema_version", "cases"}
        if unknown_top:
            raise ValueError(f"Eval cases contain unknown top-level fields: {', '.join(sorted(unknown_top))}")
        if "schema_version" in payload and payload["schema_version"] != "0.2":
            raise ValueError("Eval cases schema_version must be '0.2'")
        if "cases" not in payload:
            raise ValueError("Eval cases object is missing cases")
        payload = payload["cases"]
    if not isinstance(payload, list):
        raise ValueError("Eval cases must be a JSON array")
    cases: list[EvalCase] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise ValueError(f"Eval case {index} must be an object")
        unknown = set(item) - EVAL_CASE_FIELDS
        if unknown:
            raise ValueError(f"Eval case {index} contains unknown fields: {', '.join(sorted(unknown))}")
        for field in EVAL_BOOL_FIELDS:
            if field in item and type(item[field]) is not bool:
                raise ValueError(f"Eval case {index} field {field} must be a boolean")
        for field in EVAL_LIST_FIELDS:
            if field in item:
                _string_list(item[field], field, index)
        query = _aliased_string(item, "query", "user_task", index)
        if not query:
            raise ValueError(f"Eval case {index} is missing query")
        expected_package = _aliased_string(item, "expected_package", "expected_primary_package", index)
        expected_skill = _aliased_string(item, "expected_skill", "expected_top_skill", index)
        should_trigger = item.get("should_trigger")
        if should_trigger is None:
            should_trigger = bool(expected_package or expected_skill)
        case_id = _optional_string(item, "id", index) or f"case_{index + 1}"
        normalized_id = case_id.casefold()
        if normalized_id in seen_ids:
            raise ValueError(f"Eval case id must be unique: {case_id}")
        seen_ids.add(normalized_id)
        expected_decision = _optional_string(item, "expected_decision", index)
        if expected_decision and expected_decision not in DECISION_TYPES:
            raise ValueError(f"Eval case {index} expected_decision is invalid: {expected_decision}")
        risk_expectation = _optional_string(item, "risk_expectation", index)
        if risk_expectation not in RISK_EXPECTATIONS:
            raise ValueError(f"Eval case {index} risk_expectation is invalid: {risk_expectation}")
        importance = item.get("importance", 1)
        if type(importance) is not int or importance < 1:
            raise ValueError(f"Eval case {index} importance must be an integer >= 1")
        required_plan_skills = _string_list(item.get("required_plan_skills", []), "required_plan_skills", index)
        forbidden_skills = _string_list(item.get("forbidden_skills", []), "forbidden_skills", index)
        overlap = {value.casefold() for value in required_plan_skills} & {value.casefold() for value in forbidden_skills}
        if overlap:
            raise ValueError(f"Eval case {index} required_plan_skills overlap forbidden_skills")
        required_order = _required_order(item.get("required_order", []), index)
        required_names = {value.casefold() for value in required_plan_skills}
        if required_order and any(left.casefold() not in required_names or right.casefold() not in required_names for left, right in required_order):
            raise ValueError(f"Eval case {index} required_order endpoints must appear in required_plan_skills")
        if required_order and not _order_is_acyclic(required_order):
            raise ValueError(f"Eval case {index} required_order must be acyclic")
        if required_plan_skills and len(required_plan_skills) < 2:
            raise ValueError(f"Eval case {index} required_plan_skills must contain at least two skills")
        if expected_decision == "skill_plan" and len(required_plan_skills) < 2:
            raise ValueError(f"Eval case {index} skill_plan requires required_plan_skills")
        if required_plan_skills and expected_decision and expected_decision not in {"skill_plan", "review_required"}:
            raise ValueError(f"Eval case {index} plan requirements conflict with expected_decision")
        if not should_trigger and expected_decision in ACTIVE_DECISION_TYPES:
            raise ValueError(f"Eval case {index} inactive case conflicts with expected_decision")
        if item.get("expected_review") is True and expected_decision and expected_decision != "review_required":
            raise ValueError(f"Eval case {index} expected_review conflicts with expected_decision")
        if item.get("expected_review") is False and expected_decision == "review_required":
            raise ValueError(f"Eval case {index} expected_review conflicts with expected_decision")
        cases.append(
            EvalCase(
                query=query,
                expected_package=expected_package,
                expected_skill=expected_skill,
                id=case_id,
                category=_optional_string(item, "category", index) or "general",
                expected_related_packages=_string_list(item.get("expected_related_packages", []), "expected_related_packages", index),
                expected_top3_contains=_string_list(item.get("expected_top3_contains", []), "expected_top3_contains", index),
                should_trigger=should_trigger,
                allow_governance_review=item.get("allow_governance_review", False),
                risk_expectation=risk_expectation,
                forbid_graph_expansion=item.get("forbid_graph_expansion", False),
                ambiguous=item.get("ambiguous", False),
                expected_top2_packages=_string_list(item.get("expected_top2_packages", []), "expected_top2_packages", index),
                expected_decision=expected_decision,
                acceptable_primary_skills=_string_list(item.get("acceptable_primary_skills", []), "acceptable_primary_skills", index),
                importance=importance,
                notes=_optional_string(item, "notes", index),
                intent_family=_optional_string(item, "intent_family", index),
                profile=_optional_string(item, "profile", index),
                required_plan_skills=required_plan_skills,
                forbidden_skills=forbidden_skills,
                required_order=required_order,
                expected_review=item.get("expected_review"),
            )
        )
    return cases


def enforce_eval_gates(result: dict[str, object], path: Path) -> None:
    gate_config = _load_gate_config(path)
    raw_targets = result.get("strategies")
    targets: dict[str, object]
    if isinstance(raw_targets, dict):
        targets = raw_targets
    else:
        targets = {"default": result}
    configured_strategies = gate_config.get("strategies")
    if isinstance(configured_strategies, dict):
        selected = configured_strategies
    else:
        rules = gate_config["metrics"]
        selected = {strategy: rules for strategy in targets}
    failures: list[str] = []
    for strategy, rules in selected.items():
        current = targets.get(strategy)
        if not isinstance(current, dict):
            failures.append(f"{strategy}=missing strategy")
            continue
        metrics = current.get("metrics")
        if not isinstance(metrics, dict):
            failures.append(f"{strategy}.metrics=missing")
            continue
        for name, rule in rules.items():
            value = metrics.get(name)
            if type(value) not in {int, float} or not math.isfinite(float(value)):
                failures.append(f"{strategy}.{name}=NA")
                continue
            if "min" in rule and float(value) < rule["min"]:
                failures.append(f"{strategy}.{name}={value} < {rule['min']}")
            if "max" in rule and float(value) > rule["max"]:
                failures.append(f"{strategy}.{name}={value} > {rule['max']}")
    if failures:
        raise ValueError("evaluation gates failed: " + "; ".join(failures))


def _load_gate_config(path: Path) -> dict[str, Any]:
    payload = _load_strict_json(path, "Evaluation gate config")
    if not isinstance(payload, dict):
        raise ValueError("Evaluation gate config must be an object")
    unknown = set(payload) - {"schema_version", "metrics", "strategies"}
    if unknown:
        raise ValueError(f"Evaluation gate config contains unknown fields: {', '.join(sorted(unknown))}")
    if payload.get("schema_version", "0.2") != "0.2":
        raise ValueError("Evaluation gate config schema_version must be '0.2'")
    modes = int("metrics" in payload) + int("strategies" in payload)
    if modes != 1:
        raise ValueError("Evaluation gate config must contain exactly one of metrics or strategies")
    if "metrics" in payload:
        return {"metrics": _gate_rules(payload["metrics"], "metrics")}
    strategies = payload["strategies"]
    if not isinstance(strategies, dict) or not strategies:
        raise ValueError("Evaluation gate config strategies must be a non-empty object")
    parsed: dict[str, dict[str, dict[str, float]]] = {}
    for strategy, value in strategies.items():
        if not isinstance(strategy, str) or not strategy.strip():
            raise ValueError("Evaluation gate strategy names must be non-empty strings")
        if not isinstance(value, dict) or set(value) != {"metrics"}:
            raise ValueError(f"Evaluation gate strategy {strategy} must contain only metrics")
        parsed[strategy] = _gate_rules(value["metrics"], f"strategies.{strategy}.metrics")
    return {"strategies": parsed}


def _gate_rules(value: object, context: str) -> dict[str, dict[str, float]]:
    if not isinstance(value, dict) or not value:
        raise ValueError(f"Evaluation gate config {context} must be a non-empty object")
    parsed: dict[str, dict[str, float]] = {}
    for name, rule in value.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Evaluation gate config {context} contains an invalid metric name")
        if not isinstance(rule, dict) or not rule or set(rule) - {"min", "max"}:
            raise ValueError(f"Evaluation gate rule {context}.{name} must contain only min and/or max")
        bounds: dict[str, float] = {}
        for direction, bound in rule.items():
            if type(bound) not in {int, float} or not math.isfinite(float(bound)):
                raise ValueError(f"Evaluation gate bound {context}.{name}.{direction} must be finite")
            bounds[direction] = float(bound)
        if "min" in bounds and "max" in bounds and bounds["min"] > bounds["max"]:
            raise ValueError(f"Evaluation gate rule {context}.{name} has min greater than max")
        parsed[name] = bounds
    return parsed


def _load_strict_json(path: Path, label: str) -> object:
    raw = path.read_bytes()
    if len(raw) > MAX_EVAL_FILE_BYTES:
        raise ValueError(f"{label} exceeds {MAX_EVAL_FILE_BYTES} bytes")

    def reject_constant(value: str) -> object:
        raise ValueError(f"{label} contains non-finite number: {value}")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{label} contains duplicate object key: {key}")
            result[key] = value
        return result

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} must be UTF-8 JSON") from exc
    return json.loads(text, parse_constant=reject_constant, object_pairs_hook=unique_object)


def _optional_string(item: dict[str, object], field: str, index: int) -> str:
    if field not in item:
        return ""
    value = item[field]
    if not isinstance(value, str):
        raise ValueError(f"Eval case {index} field {field} must be a string")
    return value.strip()


def _aliased_string(item: dict[str, object], primary: str, alias: str, index: int) -> str:
    primary_value = _optional_string(item, primary, index)
    alias_value = _optional_string(item, alias, index)
    if primary_value and alias_value and primary_value != alias_value:
        raise ValueError(f"Eval case {index} fields {primary} and {alias} conflict")
    return primary_value or alias_value


def _string_list(value: object, field: str, index: int) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"Eval case {index} field {field} must be a list")
    values: list[str] = []
    seen: set[str] = set()
    for position, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"Eval case {index} field {field}[{position}] must be a non-empty string")
        normalized = item.strip()
        key = normalized.casefold()
        if key in seen:
            raise ValueError(f"Eval case {index} field {field} contains duplicate value: {normalized}")
        seen.add(key)
        values.append(normalized)
    return tuple(values)


def _required_order(value: object, index: int) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        raise ValueError(f"Eval case {index} field required_order must be a list")
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for position, item in enumerate(value):
        if not isinstance(item, list) or len(item) != 2:
            raise ValueError(f"Eval case {index} field required_order[{position}] must be a two-item list")
        if any(not isinstance(part, str) or not part.strip() for part in item):
            raise ValueError(f"Eval case {index} field required_order[{position}] must contain non-empty strings")
        pair = (item[0].strip(), item[1].strip())
        key = (pair[0].casefold(), pair[1].casefold())
        if key[0] == key[1]:
            raise ValueError(f"Eval case {index} field required_order[{position}] cannot order a skill before itself")
        if key in seen:
            raise ValueError(f"Eval case {index} field required_order contains duplicate pair")
        seen.add(key)
        pairs.append(pair)
    return tuple(pairs)


def _order_is_acyclic(pairs: tuple[tuple[str, str], ...]) -> bool:
    adjacency: dict[str, set[str]] = defaultdict(set)
    indegree: Counter[str] = Counter()
    for before, after in pairs:
        left = before.casefold()
        right = after.casefold()
        if right not in adjacency[left]:
            adjacency[left].add(right)
            indegree[right] += 1
        indegree.setdefault(left, 0)
    ready = [node for node, degree in indegree.items() if degree == 0]
    visited = 0
    while ready:
        node = ready.pop()
        visited += 1
        for target in adjacency.get(node, set()):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    return visited == len(indegree)


def load_records_from_vault(vault: Path) -> tuple[list[SkillRecord], list[str]]:
    records, _ = load_routing_index(vault)
    return records, []


def _rank(query: str, records: list[SkillRecord]) -> list[SkillRecord]:
    return _ranked_result(query, records).records


def _ranked_result(
    query: str,
    records: list[SkillRecord],
    *,
    strategy: str = "legacy",
    edges: list[Any] | None = None,
    profile: str = "",
) -> RankedResult:
    result = rank_records(query, records, strategy=strategy, profile=profile)
    return apply_graph_shadow(result, edges or [], records) if strategy == "hybrid-graph" else result


def _query_context(query: str) -> tuple[set[str], dict[str, tuple[str, ...]], bool]:
    lowered = query.lower()
    terms = set(_terms(query))
    evidence: dict[str, list[str]] = defaultdict(list)
    for needle, phrase, packages, extra_terms in CHINESE_QUERY_SIGNALS:
        lowered_needle = needle.lower()
        if lowered_needle not in lowered or _is_negated_signal(lowered, lowered_needle):
            continue
        terms.update(_terms(phrase))
        terms.update(extra_terms)
        for package in packages:
            evidence[package].append(f"{needle}->{phrase}")
    return terms, {package: tuple(values) for package, values in evidence.items()}, _has_constraint(query)


def _is_negated_signal(lowered: str, needle: str) -> bool:
    index = lowered.find(needle)
    if index < 0:
        return False
    before = lowered[max(0, index - 10):index]
    return any(marker in before for marker in ["不要", "不需要", "无需", "不是", "do not", "don't", "not "])


def _has_constraint(query: str) -> bool:
    lowered = query.lower()
    return any(phrase in lowered for phrase in CONSTRAINT_PHRASES)


def _first_heading(text: str) -> str:
    match = re.search(r"^#\s+(.+?)\s*$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _field_value(text: str, field: str) -> str:
    match = re.search(rf"^- \*\*{re.escape(field)}\*\*: (.+)$", text, re.MULTILINE)
    if match:
        return match.group(1).strip()
    match = re.search(rf"^- {re.escape(field)}: (.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _section_text(text: str, heading: str) -> str:
    match = re.search(rf"^## {re.escape(heading)}\s*\n(.*?)(?:\n## |\Z)", text, re.MULTILINE | re.DOTALL)
    return " ".join(match.group(1).split()) if match else ""


def _split_field(value: str) -> list[str]:
    if not value or value.strip() == "-":
        return []
    return [part.strip(" .") for part in re.split(r"[,;|]", value) if part.strip(" .")]


def _float_value(value: str, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _case_results(
    ranked_cases: list[tuple[RankedResult, EvalCase]],
    skill_edges: list[Any],
    route_statuses: dict[str, str] | None = None,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    failures: list[dict[str, str]] = []
    borderline: list[dict[str, str]] = []
    for result, case in ranked_cases:
        ranked = result.records
        top = ranked[0] if ranked else None
        top3 = ranked[:3]
        top_packages = _top_packages(result, 5)
        base = _case_base(result, case, skill_edges)
        if _expects_ambiguity(case):
            if _ambiguous_reasons(result):
                borderline.append({**base, "borderline_type": "ambiguity_flagged"})
            else:
                failures.append({**base, "failure_type": "ambiguity_not_flagged"})
        if _overconfident_top1(result, case):
            failures.append({**base, "failure_type": "overconfident_top1"})
        if result.constraint_detected and case.forbid_graph_expansion and not _graph_over_expanded(result, case, skill_edges):
            borderline.append({**base, "borderline_type": "constraint_respected"})
        elif result.constraint_detected and _graph_over_expanded(result, case, skill_edges):
            failures.append({**base, "failure_type": "constraint_not_respected"})

        base = {
            **base,
            "id": case.id,
            "category": case.category,
            "query": case.query,
            "expected_package": case.expected_package,
            "expected_skill": case.expected_skill,
            "actual_skill": top.id if top else "",
            "actual_skill_name": top.name if top else "",
            "actual_package": top.package if top else "",
            "top3": ", ".join(record.name for record in top3),
            "top_packages": ", ".join(top_packages),
        }
        if not case.should_trigger:
            route_status = (route_statuses or {}).get(case.id, "")
            if route_status and route_status != "continue_without_skill":
                failures.append({**base, "failure_type": "false_trigger"})
            elif top and case.allow_governance_review and top.package == "safety-governance":
                borderline.append({**base, "borderline_type": "governance_review_for_unsupported_task"})
            continue
        if case.expected_package and (not top or top.package != case.expected_package):
            if case.expected_package in top_packages:
                borderline.append({**base, "borderline_type": "package_not_top1_but_present"})
            else:
                failures.append({**base, "failure_type": "package_top1_miss"})
        if case.expected_skill and (not top or not _skill_matches(top, case.expected_skill)):
            if any(_skill_matches(record, case.expected_skill) for record in top3):
                borderline.append({**base, "borderline_type": "skill_not_top1_but_top3"})
            else:
                failures.append({**base, "failure_type": "skill_top3_miss"})
        for expected in case.expected_top3_contains:
            if not any(_skill_matches(record, expected) for record in top3):
                borderline.append({**base, "borderline_type": f"expected_top3_missing:{expected}"})
        if case.expected_related_packages and not _cross_package_hit(result, case):
            failures.append({**base, "failure_type": "cross_package_related_miss"})
        if case.risk_expectation and (not top or top.risk_level != case.risk_expectation):
            failures.append({**base, "failure_type": "risk_expectation_miss", "expected_risk": case.risk_expectation, "actual_risk": top.risk_level if top else ""})
        if _graph_over_expanded(result, case, skill_edges):
            failures.append({**base, "failure_type": "graph_over_expansion"})
    return failures, borderline


def write_agent_first_eval_report(result: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_agent_first_eval_markdown(result), encoding="utf-8", newline="\n")


def _agent_first_summary(rows: list[dict[str, Any]], warnings: list[str]) -> dict[str, Any]:
    recommendation_rows = [row for row in rows if row["actual_decision"] in ACTIVE_DECISION_TYPES]
    trigger_rows = [row for row in rows if row["expected_decision"] in ACTIVE_DECISION_TYPES]
    continue_rows = [row for row in rows if row["expected_decision"] == "continue_without_skill"]
    clarify_rows = [row for row in rows if row["expected_decision"] == "clarify_required"]
    non_clarify_rows = [row for row in rows if row["expected_decision"] != "clarify_required"]
    negative_rows = [row for row in rows if row["negative_case"]]
    high_risk_rows = [row for row in rows if row["expected_decision"] == "review_required"]
    cross_rows = [row for row in rows if row["expects_cross_package"] and row["expected_decision"] in ACTIVE_DECISION_TYPES]
    constraint_rows = [row for row in rows if row["category"] == "graph_constraint" or row["has_forbidden"]]
    plan_rows = [row for row in rows if row["plan_required"]]
    plan_order_rows = [row for row in plan_rows if row["plan_order_required"]]
    plan_review_rows = [row for row in plan_rows if row["plan_review_labeled"]]
    primary_hits = sum(1 for row in recommendation_rows if row["primary_ok"])
    candidate_hits = sum(row["candidate_hits"] for row in trigger_rows)
    candidate_required = sum(row["candidate_required"] for row in trigger_rows)
    candidate_case_rows = [row for row in trigger_rows if row["candidate_hit_at_3"] is not None]
    candidate_case_hits = sum(1 for row in candidate_case_rows if row["candidate_hit_at_3"])
    continue_hits = sum(1 for row in continue_rows if row["actual_decision"] == "continue_without_skill")
    clarification_hits = sum(1 for row in clarify_rows if row["actual_decision"] == "clarify_required")
    over_clarifications = sum(1 for row in non_clarify_rows if row["actual_decision"] == "clarify_required")
    false_activations = sum(1 for row in negative_rows if row["actual_decision"] in ACTIVE_DECISION_TYPES)
    review_hits = sum(1 for row in high_risk_rows if row["actual_decision"] == "review_required")
    cross_hits = sum(1 for row in cross_rows if row["related_ok"])
    constraint_hits = sum(1 for row in constraint_rows if row["constraint_ok"])
    decision_hits = sum(1 for row in rows if row["decision_ok"])
    plan_hits = sum(row["plan_hits"] for row in plan_rows)
    plan_required = sum(row["plan_required"] for row in plan_rows)
    plan_actual = sum(row["plan_actual"] for row in plan_rows)
    plan_precision_hits = sum(row["plan_precision_hits"] for row in plan_rows)
    plan_order_hits = sum(1 for row in plan_order_rows if row["plan_order_ok"])
    plan_review_hits = sum(1 for row in plan_review_rows if row["plan_review_ok"])
    graph_pairs = [
        row
        for row in trigger_rows
        if row.get("candidate_hit_at_3") is not None and row.get("lexical_candidate_hit_at_3") is not None
    ]
    graph_gain = (
        round(
            sum(int(row["candidate_hit_at_3"]) - int(row["lexical_candidate_hit_at_3"]) for row in graph_pairs)
            / len(graph_pairs),
            4,
        )
        if graph_pairs else None
    )
    tokens = [row["tokens"] for row in rows]
    waste_tokens = sum(
        row["candidate_tokens"]
        for row in rows
        if not row["decision_ok"]
        or (row["actual_decision"] in ACTIVE_DECISION_TYPES and not row["primary_ok"])
    )
    failures = [
        row
        for row in rows
        if not row["decision_ok"]
        or (row["actual_decision"] in ACTIVE_DECISION_TYPES and not row["primary_ok"])
        or (row["expected_decision"] in ACTIVE_DECISION_TYPES and not row["recall_ok"])
        or not row["related_ok"]
        or not row["constraint_ok"]
        or (row["plan_required"] and row["plan_recall"] != 1.0)
        or not row["plan_order_ok"]
        or not row["plan_review_ok"]
    ]
    patterns = Counter()
    for row in failures:
        if not row["decision_ok"]:
            patterns["decision_type_mismatch"] += 1
        if row["actual_decision"] in ACTIVE_DECISION_TYPES and not row["primary_ok"]:
            patterns["primary_skill_miss"] += 1
        if row["expected_decision"] in ACTIVE_DECISION_TYPES and not row["recall_ok"]:
            patterns["candidate_recall_miss"] += 1
        if not row["related_ok"]:
            patterns["cross_package_related_miss"] += 1
        if not row["constraint_ok"]:
            patterns["constraint_not_respected"] += 1
        if row["plan_required"] and row["plan_recall"] != 1.0:
            patterns["plan_skill_miss"] += 1
        if not row["plan_order_ok"]:
            patterns["plan_order_miss"] += 1
        if not row["plan_review_ok"]:
            patterns["plan_review_miss"] += 1
    red_rows = [row for row in rows if row["source"] == "red_team"]
    audit_rows = [row for row in rows if row["source"] == "manual_audit"]
    return {
        "cases": len(rows),
        "warnings": warnings,
        "metrics": {
            "primary_skill_precision_at_1": _rate(primary_hits, len(recommendation_rows)),
            "candidate_recall_at_3": _rate(candidate_case_hits, len(candidate_case_rows)),
            "candidate_required_coverage_at_3": _rate(candidate_hits, candidate_required),
            "correct_continue_rate": _rate(continue_hits, len(continue_rows)),
            "no_skill_recall": _rate(continue_hits, len(continue_rows)),
            "clarification_recall": _rate(clarification_hits, len(clarify_rows)),
            "over_clarification_rate": _rate(over_clarifications, len(non_clarify_rows)),
            "decision_accuracy": _rate(decision_hits, len(rows)),
            "false_activation_rate": _rate(false_activations, len(negative_rows)),
            "high_risk_review_required_rate": _rate(review_hits, len(high_risk_rows)),
            "cross_package_related_hit": _rate(cross_hits, len(cross_rows)),
            "constraint_respect_rate": _rate(constraint_hits, len(constraint_rows)),
            "plan_skill_recall": _rate(plan_hits, plan_required),
            "plan_skill_precision": _rate(plan_precision_hits, plan_actual),
            "plan_order_validity": _rate(plan_order_hits, len(plan_order_rows)),
            "plan_review_accuracy": _rate(plan_review_hits, len(plan_review_rows)),
            "graph_incremental_top3_gain": graph_gain,
            "average_response_tokens_estimate": round(sum(tokens) / len(tokens), 2) if tokens else 0.0,
            "p95_response_tokens_estimate": _p95(tokens),
            "candidate_payload_waste_tokens": waste_tokens,
        },
        "counts": {
            "red_team_cases": len(red_rows),
            "manual_audit_cases": len(audit_rows),
            "recommendations": len(recommendation_rows),
            "trigger_cases": len(trigger_rows),
            "continue_cases": len(continue_rows),
            "clarify_cases": len(clarify_rows),
            "negative_cases": len(negative_rows),
            "false_activations": false_activations,
            "high_risk_cases": len(high_risk_rows),
            "cross_package_cases": len(cross_rows),
            "constraint_cases": len(constraint_rows),
            "plan_cases": len(plan_rows),
            "plan_required_skills": plan_required,
            "intent_families": len({row["intent_family"] for row in rows if row["intent_family"]}),
        },
        "scorecards": {
            "balanced_coverage": _balanced_scorecard(red_rows),
            "real_workload": _real_workload_scorecard(audit_rows),
        },
        "intent_family_audit": _intent_family_audit(rows),
        "failures": failures,
        "case_details": rows,
        "top_failure_patterns": dict(sorted(patterns.items())),
    }


def _expected_route_status(case: EvalCase) -> str:
    if case.expected_decision:
        return case.expected_decision
    if not case.should_trigger:
        return "continue_without_skill"
    if case.ambiguous:
        return "clarify_required"
    if case.expected_review is True:
        return "review_required"
    if case.required_plan_skills:
        return "skill_plan"
    if case.expected_review is False:
        return "recommend"
    if case.risk_expectation == "high":
        return "review_required"
    return "recommend"


def _actual_decision_type(decision: dict[str, Any]) -> str:
    structured = decision.get("decision")
    if not isinstance(structured, dict):
        return ""
    value = structured.get("type")
    return value if isinstance(value, str) and value in DECISION_TYPES else ""


def _legacy_status_for_decision(decision_type: str) -> str:
    if decision_type == "clarify_required":
        return "continue_without_skill"
    if decision_type == "skill_plan":
        return "recommend"
    return decision_type


def _skill_value_matches(skill_id: str, name: str, expected: set[str]) -> bool:
    values = {skill_id.strip().lower(), name.strip().lower()}
    return bool(values & {value.strip().lower() for value in expected})


def _decision_candidates(decision: dict[str, Any], *, limit: int | None = None) -> list[dict[str, Any]]:
    primary = decision.get("primary") or {}
    plan = [
        step.get("skill")
        for step in decision.get("plan", [])
        if isinstance(step, dict) and isinstance(step.get("skill"), dict)
    ]
    candidates = _unique_candidates(
        [item for item in [primary, *(decision.get("alternatives") or []), *plan] if isinstance(item, dict) and item]
    )
    return candidates[:limit] if limit is not None else candidates


def _unique_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in candidates:
        key = str(item.get("id") or item.get("name") or "").strip().casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _candidate_matches_expected(candidate: dict[str, Any], expected: str) -> bool:
    return _skill_value_matches(
        str(candidate.get("id", "")),
        str(candidate.get("name", "")),
        {expected},
    )


def _required_candidate_skills(case: EvalCase) -> tuple[str, ...]:
    if case.expected_top3_contains:
        return case.expected_top3_contains
    if case.acceptable_primary_skills:
        return case.acceptable_primary_skills
    if case.expected_skill:
        return (case.expected_skill,)
    if case.required_plan_skills:
        return case.required_plan_skills
    return ()


def _candidate_recall_counts(decision: dict[str, Any], case: EvalCase) -> tuple[int, int]:
    candidates = _decision_candidates(decision, limit=3)
    required = _required_candidate_skills(case)
    if required:
        hits = sum(
            1
            for expected in required
            if any(_candidate_matches_expected(candidate, expected) for candidate in candidates)
        )
        return hits, len(required)
    if case.expected_package:
        return int(any(candidate.get("package") == case.expected_package for candidate in candidates)), 1
    return 0, 0


def _plan_order_ok(plan_skills: list[dict[str, Any]], required_order: tuple[tuple[str, str], ...]) -> bool:
    def position(expected: str) -> int | None:
        return next(
            (index for index, candidate in enumerate(plan_skills) if _candidate_matches_expected(candidate, expected)),
            None,
        )

    for before, after in required_order:
        before_position = position(before)
        after_position = position(after)
        if before_position is None or after_position is None or before_position >= after_position:
            return False
    return True


def _p95(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, (len(ordered) * 95 + 99) // 100 - 1))]


def _balanced_scorecard(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_package: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["expected_package"] and row["primary_applicable"]:
            by_package[row["expected_package"]].append(row)
    scores = [sum(1 for row in group if row["primary_ok"]) / len(group) for group in by_package.values() if group]
    return {"cases": len(rows), "packages": len(by_package), "macro_primary_accuracy": round(sum(scores) / len(scores), 4) if scores else 0.0}


def _real_workload_scorecard(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_weight = sum(row["importance"] for row in rows)
    primary_weight = sum(row["importance"] for row in rows if row["primary_ok"])
    decision_weight = sum(row["importance"] for row in rows if row["decision_ok"])
    return {
        "available": bool(rows),
        "cases": len(rows),
        "weighted_primary_accuracy": _rate(primary_weight, total_weight),
        "weighted_decision_accuracy": _rate(decision_weight, total_weight),
    }


def _intent_family_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_source: dict[str, set[str]] = defaultdict(set)
    unlabeled = 0
    for row in rows:
        family = str(row.get("intent_family", "")).strip()
        if family:
            by_source[str(row.get("source", ""))].add(family)
        else:
            unlabeled += 1
    source_sets = list(by_source.values())
    cross_source = set.intersection(*source_sets) if len(source_sets) > 1 else set()
    all_families = set().union(*source_sets) if source_sets else set()
    return {
        "labeled_cases": len(rows) - unlabeled,
        "unlabeled_cases": unlabeled,
        "unique_families": len(all_families),
        "families_by_source": {key: sorted(value) for key, value in sorted(by_source.items())},
        "cross_source_families": sorted(cross_source),
    }


def _agent_first_eval_markdown(result: dict[str, Any]) -> str:
    metrics = result["metrics"]
    counts = result["counts"]
    scorecards = result["scorecards"]
    lines = [
        "# Agent-First Route Eval Report",
        "",
        "This report measures routing decisions and routing payload cost. It does not claim skill execution or final task completion.",
        "",
        "## Summary",
        "",
        f"- total cases: {result['cases']}",
        f"- Primary Skill Precision@1: {_format_rate(metrics['primary_skill_precision_at_1'])}",
        f"- Candidate Recall@3: {_format_rate(metrics['candidate_recall_at_3'])}",
        f"- Candidate Required Coverage@3: {_format_rate(metrics['candidate_required_coverage_at_3'])}",
        f"- Correct Continue Rate: {_format_rate(metrics['correct_continue_rate'])}",
        f"- Clarification Recall: {_format_rate(metrics['clarification_recall'])}",
        f"- Over-Clarification Rate: {_format_rate(metrics['over_clarification_rate'])}",
        f"- False Activation Rate: {_format_rate(metrics['false_activation_rate'])}",
        f"- High-Risk Review Required Rate: {_format_rate(metrics['high_risk_review_required_rate'])}",
        f"- Cross-Package Related Hit: {_format_rate(metrics['cross_package_related_hit'])}",
        f"- Constraint Respect Rate: {_format_rate(metrics['constraint_respect_rate'])}",
        f"- Plan Skill Recall: {_format_rate(metrics['plan_skill_recall'])}",
        f"- Plan Skill Precision: {_format_rate(metrics['plan_skill_precision'])}",
        f"- Plan Order Validity: {_format_rate(metrics['plan_order_validity'])}",
        f"- Plan Review Accuracy: {_format_rate(metrics['plan_review_accuracy'])}",
        f"- Average Response Token Estimate: {metrics['average_response_tokens_estimate']:.2f}",
        f"- P95 Response Token Estimate: {metrics['p95_response_tokens_estimate']}",
        f"- Candidate Payload Waste Tokens: {metrics['candidate_payload_waste_tokens']}",
        "",
        "## Evidence Sources",
        "",
        *_mapping_lines(counts),
        "",
        "## Scorecards",
        "",
        "### Balanced Coverage",
        "",
        *_mapping_lines(scorecards["balanced_coverage"]),
        "",
        "### Real Workload",
        "",
        *_mapping_lines(scorecards["real_workload"]),
        "",
        "## Failed Cases",
        "",
        _agent_first_case_table(result["failures"]),
        "",
        "## Top Failure Patterns",
        "",
        *_mapping_lines(result["top_failure_patterns"]),
        "",
        "## Case Details",
        "",
        _agent_first_case_table(result["case_details"]),
        "",
    ]
    return "\n".join(lines)


def _agent_first_case_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "- None"
    lines = [
        "| Source | ID | Category | Expected | Actual | Legacy Status | Primary | Primary OK | Recall OK | Tokens |",
        "|---|---|---|---|---|---|---|---|---|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {_cell(row['source'])} | {_cell(row['id'])} | {_cell(row['category'])} | {_cell(row['expected_decision'])} | "
            f"{_cell(row['actual_decision'])} | {_cell(row['actual_status'])} | {_cell(row['actual_skill_name'])} | "
            f"{row['primary_ok']} | {row['recall_ok']} | {row['tokens']} |"
        )
    return "\n".join(lines)


def write_eval_report(result: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_eval_report_markdown(result), encoding="utf-8", newline="\n")


def _eval_report_markdown(result: dict[str, Any]) -> str:
    metrics = result.get("metrics", {})
    counts = result.get("counts", {})
    lines = [
        "# Adversarial Eval Report",
        "",
        "This report is failure-focused. Lower metrics are useful evidence of brittle routing behavior.",
        "",
        "## Summary",
        "",
        f"- total cases: {result.get('cases', 0)}",
        f"- total skills: {result.get('skills', 0)}",
        f"- Package Hit@1: {_format_rate(metrics.get('package_hit_at_1'))}",
        f"- Package Hit@2: {_format_rate(metrics.get('package_hit_at_2'))}",
        f"- Skill Hit@1: {_format_rate(metrics.get('skill_hit_at_1'))}",
        f"- Skill Hit@3: {_format_rate(metrics.get('skill_hit_at_3'))}",
        f"- False Trigger Rate: {_format_rate(metrics.get('false_trigger_rate'))}",
        f"- Ambiguity Correctly Flagged: {_format_rate(metrics.get('ambiguity_correctly_flagged'))}",
        f"- Overconfident Top1 Count: {metrics.get('overconfident_top1_count', 0)}",
        f"- Cross-Package Hit: {_format_rate(metrics.get('cross_package_hit'))}",
        f"- Cross-Package Related Hit: {_format_rate(metrics.get('cross_package_related_hit', metrics.get('cross_package_hit')))}",
        f"- Constraint Respect Rate: {_format_rate(metrics.get('constraint_respect_rate'))}",
        f"- Graph Over-Expansion Count: {metrics.get('graph_over_expansion_count', 0)}",
        f"- Graph Over-Expansion Delta: {_format_rate(metrics.get('graph_over_expansion_delta'))}",
        f"- Graph Candidate Leakage Count: {_format_rate(metrics.get('graph_candidate_leakage_count'))}",
        f"- Graph Lift Cap Violation Count: {_format_rate(metrics.get('graph_lift_cap_violation_count'))}",
        f"- Risk Expectation Match Rate: {_format_rate(metrics.get('risk_expectation_match_rate'))}",
        "",
        "## Cases By Category",
        "",
        *_mapping_lines(result.get("cases_by_category", {})),
        "",
        "## Counts",
        "",
        *_mapping_lines(counts),
        "",
        "## Failed Cases",
        "",
        _case_table(result.get("failures", []), failure=True),
        "",
        "## Borderline Cases",
        "",
        _case_table(result.get("borderline_cases", []), failure=False),
        "",
        "## Case Details",
        "",
        _case_detail_table(result.get("case_details", [])),
        "",
        "## Top Failure Patterns",
        "",
        *_mapping_lines(result.get("top_failure_patterns", {})),
        "",
        "## Recommended Next Rule Changes",
        "",
        *_recommendations(result),
        "",
    ]
    return "\n".join(lines)


def _mapping_lines(values: Any) -> list[str]:
    if not isinstance(values, dict) or not values:
        return ["- None"]
    return [f"- {_cell(key)}: {_cell(value)}" for key, value in sorted(values.items())]


def _case_table(cases: Any, failure: bool) -> str:
    if not isinstance(cases, list) or not cases:
        return "- None"
    label = "Failure" if failure else "Borderline"
    rows = [
        f"| ID | Category | {label} Type | Task | Expected | Actual | Top-2 Packages | Ambiguity Reasons | Constraint |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for case in cases:
        case_type = case.get("failure_type") or case.get("borderline_type") or ""
        expected = case.get("expected_skill") or case.get("expected_package") or case.get("expected_risk") or "no_trigger"
        actual = case.get("actual_skill_name") or case.get("actual_package") or ""
        rows.append(
            f"| {_cell(case.get('id', ''))} | {_cell(case.get('category', ''))} | {_cell(case_type)} | "
            f"{_cell(case.get('query', ''))} | {_cell(expected)} | {_cell(actual)} | "
            f"{_cell(case.get('top2_package_candidates', ''))} | {_cell(case.get('ambiguity_reasons', ''))} | "
            f"{_cell(case.get('constraint_detected', ''))}/{_cell(case.get('constraint_respected', ''))} |"
        )
    return "\n".join(rows)


def _case_detail_table(cases: Any) -> str:
    if not isinstance(cases, list) or not cases:
        return "- None"
    rows = [
        "| ID | Category | Top-2 Package Candidates | Expected Related | Actual Related | Related Evidence | Missing Related Reason |",
        "|---|---|---|---|---|---|---|",
    ]
    for case in cases:
        rows.append(
            f"| {_cell(case.get('id', ''))} | {_cell(case.get('category', ''))} | "
            f"{_cell(case.get('top2_package_candidates', ''))} | {_cell(case.get('expected_related_packages', ''))} | "
            f"{_cell(case.get('actual_related_packages', ''))} | {_cell(case.get('related_evidence', ''))} | "
            f"{_cell(case.get('missing_related_reason', ''))} |"
        )
    return "\n".join(rows)


def _recommendations(result: dict[str, Any]) -> list[str]:
    patterns = result.get("top_failure_patterns", {})
    recommendations: list[str] = []
    if patterns.get("package_top1_miss") or patterns.get("cross_package_related_miss"):
        recommendations.append("- Add ambiguity-aware package ranking and expose top package candidates instead of only top-1.")
    if patterns.get("overconfident_top1") or patterns.get("ambiguity_not_flagged"):
        recommendations.append("- Use ambiguity reasons and top-2 package candidates when package evidence is close.")
    if patterns.get("false_trigger"):
        recommendations.append("- Add unsupported/high-risk no-trigger handling before skill recommendation.")
    if patterns.get("risk_expectation_miss"):
        recommendations.append("- Split risk inference between mentions of risk topics and actions that perform risky behavior.")
    if patterns.get("graph_over_expansion") or patterns.get("constraint_not_respected"):
        recommendations.append("- Gate graph expansion on explicit user permission when tasks say no follow-up workflow.")
    if not recommendations:
        recommendations.append("- No dominant failure pattern found; expand adversarial cases before tuning rules.")
    return recommendations


def _failure_patterns(failures: list[dict[str, str]]) -> dict[str, int]:
    return dict(sorted(Counter(item.get("failure_type", "unknown") for item in failures).items()))


def _case_base(result: RankedResult, case: EvalCase, skill_edges: list[Any]) -> dict[str, str]:
    top = result.records[0] if result.records else None
    related_packages = _actual_related_packages(result, case)
    return {
        "id": case.id,
        "category": case.category,
        "query": case.query,
        "expected_package": case.expected_package,
        "expected_skill": case.expected_skill,
        "actual_skill": top.id if top else "",
        "actual_skill_name": top.name if top else "",
        "actual_package": top.package if top else "",
        "top3": ", ".join(record.name for record in result.records[:3]),
        "top_packages": ", ".join(_top_packages(result, 5)),
        "top2_package_candidates": _candidate_text(result.package_candidates[:2]),
        "ambiguity_reasons": "; ".join(_ambiguous_reasons(result)),
        "constraint_detected": str(result.constraint_detected),
        "constraint_respected": str(not _graph_over_expanded(result, case, skill_edges)) if (result.constraint_detected or case.forbid_graph_expansion) else "",
        "expected_related_packages": ", ".join(case.expected_related_packages),
        "actual_related_packages": ", ".join(related_packages),
        "related_evidence": _related_evidence(result, case),
        "missing_related_reason": "" if set(case.expected_related_packages) & set(related_packages) else _missing_related_reason(result, case),
    }


def _case_details(ranked_cases: list[tuple[RankedResult, EvalCase]], skill_edges: list[Any]) -> list[dict[str, str]]:
    return [_case_base(result, case, skill_edges) for result, case in ranked_cases]


def _candidate_text(candidates: list[PackageCandidate]) -> str:
    return "; ".join(f"{candidate.package}={candidate.score} ({', '.join(candidate.evidence)})" for candidate in candidates)


def _top_packages(result: RankedResult, limit: int) -> list[str]:
    packages = [candidate.package for candidate in result.package_candidates[:limit]]
    for package in _unique_packages(result.records[:limit]):
        if package not in packages:
            packages.append(package)
    return packages[:limit]


def _expects_ambiguity(case: EvalCase) -> bool:
    return case.ambiguous or case.category == "ambiguous" or bool(case.expected_top2_packages)


def _ambiguous_reasons(result: RankedResult) -> list[str]:
    reasons: list[str] = []
    if len(result.package_candidates) >= 2:
        first, second = result.package_candidates[0], result.package_candidates[1]
        if second.score >= 1.0 and (first.score - second.score) <= 1.5:
            reasons.append(f"close package evidence: {first.package}={first.score}, {second.package}={second.score}")
    return reasons


def _overconfident_top1(result: RankedResult, case: EvalCase) -> bool:
    if not result.records or not _expects_ambiguity(case):
        return False
    return not _ambiguous_reasons(result)


def _package_hit_at(result: RankedResult, case: EvalCase, limit: int) -> bool:
    if not case.expected_package or not case.should_trigger:
        return False
    return case.expected_package in _top_packages(result, limit)


def _actual_related_packages(result: RankedResult, case: EvalCase) -> list[str]:
    top = result.records[0] if result.records else None
    return [package for package in _top_packages(result, 5) if package and package != (top.package if top else "")]


def _related_evidence(result: RankedResult, case: EvalCase) -> str:
    rows = []
    for package in case.expected_related_packages:
        evidence = result.package_evidence.get(package, ())
        if evidence:
            rows.append(f"{package}: {', '.join(evidence)}")
    return "; ".join(rows)


def _missing_related_reason(result: RankedResult, case: EvalCase) -> str:
    if not case.expected_related_packages:
        return ""
    missing = [package for package in case.expected_related_packages if package not in _top_packages(result, 5)]
    if not missing:
        return ""
    return "no top-5 package evidence for " + ", ".join(missing)


def _is_false_trigger(ranked: list[SkillRecord], case: EvalCase) -> bool:
    if not ranked:
        return False
    top = ranked[0]
    if case.allow_governance_review and top.package == "safety-governance":
        return False
    return True


def _cross_package_hit(result: RankedResult, case: EvalCase) -> bool:
    if not case.expected_related_packages or not result.records:
        return False
    return bool(set(case.expected_related_packages) & set(_top_packages(result, 5)))


def _graph_over_expansions(ranked_cases: list[tuple[RankedResult, EvalCase]], skill_edges: list[Any]) -> int:
    return sum(1 for result, case in ranked_cases if _graph_over_expanded(result, case, skill_edges))


def _graph_over_expanded(result: RankedResult, case: EvalCase, skill_edges: list[Any]) -> bool:
    if not (case.forbid_graph_expansion or result.constraint_detected) or not result.records:
        return False
    top = result.records[0]
    allowed = {case.expected_package, *case.expected_related_packages, ""}
    if result.constraint_detected:
        explicit_packages = [
            candidate.package
            for candidate in result.package_candidates[1:4]
            if any("->" in evidence for evidence in candidate.evidence)
        ]
        package_expanded = any(package not in allowed for package in explicit_packages)
        workflow_expanded = False
    else:
        package_expanded = any(package not in allowed for package in _top_packages(result, 4)[1:])
        workflow_expanded = case.forbid_graph_expansion and any(
            edge.source == top.id and edge.relation == "workflow_next" and edge.review_state == "confirmed" for edge in skill_edges
        )
    return package_expanded or workflow_expanded


def _unique_packages(records: list[SkillRecord]) -> list[str]:
    seen: list[str] = []
    for record in records:
        if record.package and record.package not in seen:
            seen.append(record.package)
    return seen


def _cell(value: object) -> str:
    return markdown_plain(value)


def _terms(value: str) -> set[str]:
    stop = {"a", "an", "and", "for", "from", "in", "of", "or", "the", "to", "use", "when", "with"}
    return {term for term in re.findall(r"[a-z0-9]+", value.lower()) if len(term) > 1 and term not in stop}


def _skill_matches(record: SkillRecord, expected: str) -> bool:
    normalized = expected.strip().lower()
    return normalized in {record.id.lower(), record.name.lower()}


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def _ranking_quality(ranked_cases: list[tuple[RankedResult, EvalCase]]) -> dict[str, float | None]:
    reciprocal: list[float] = []
    ndcg: list[float] = []
    for result, case in ranked_cases:
        if not case.expected_skill or not case.should_trigger:
            continue
        rank = next((index for index, record in enumerate(result.records, 1) if _skill_matches(record, case.expected_skill)), None)
        reciprocal.append(1.0 / rank if rank else 0.0)
        ndcg.append(1.0 / math.log2(rank + 1) if rank and rank <= 5 else 0.0)
    return {
        "mrr": round(sum(reciprocal) / len(reciprocal), 4) if reciprocal else None,
        "ndcg_at_5": round(sum(ndcg) / len(ndcg), 4) if ndcg else None,
    }


def _calibration_quality(ranked_cases: list[tuple[RankedResult, EvalCase]]) -> dict[str, Any]:
    rows: list[tuple[float, int]] = []
    for result, case in ranked_cases:
        score = result.scored_records[0][0] if result.scored_records else 0.0
        probability = max(0.0, min(1.0, score / (score + 2.0)))
        if not case.should_trigger:
            outcome = 0
        elif case.expected_skill:
            outcome = int(bool(result.records and _skill_matches(result.records[0], case.expected_skill)))
        elif case.expected_package:
            outcome = int(bool(result.records and result.records[0].package == case.expected_package))
        else:
            continue
        rows.append((probability, outcome))
    if not rows:
        return {"brier_score": None, "ece": None, "coverage_risk_curve": []}
    brier = sum((probability - outcome) ** 2 for probability, outcome in rows) / len(rows)
    ece = 0.0
    for low in (0.0, 0.2, 0.4, 0.6, 0.8):
        bucket = [(p, y) for p, y in rows if low <= p < low + 0.2 or (low == 0.8 and p == 1.0)]
        if bucket:
            ece += len(bucket) / len(rows) * abs(sum(p for p, _ in bucket) / len(bucket) - sum(y for _, y in bucket) / len(bucket))
    curve = []
    for threshold in (0.4, 0.5, 0.6, 0.7, 0.8):
        selected = [(p, y) for p, y in rows if p >= threshold]
        curve.append({
            "threshold": threshold,
            "coverage": round(len(selected) / len(rows), 4),
            "risk": round(1.0 - sum(y for _, y in selected) / len(selected), 4) if selected else None,
        })
    return {"brier_score": round(brier, 4), "ece": round(ece, 4), "coverage_risk_curve": curve}


def _macro_package_accuracy(ranked_cases: list[tuple[RankedResult, EvalCase]]) -> float | None:
    grouped: dict[str, list[int]] = defaultdict(list)
    for result, case in ranked_cases:
        if case.expected_package and case.should_trigger:
            grouped[case.expected_package].append(int(bool(result.records and result.records[0].package == case.expected_package)))
    return round(sum(sum(values) / len(values) for values in grouped.values()) / len(grouped), 4) if grouped else None


def _graph_incremental_gain(
    records: list[SkillRecord],
    graph_cases: list[tuple[RankedResult, EvalCase]],
    edges: list[Any],
) -> float | None:
    eligible = [(result, case) for result, case in graph_cases if case.expected_skill and case.should_trigger]
    if not eligible:
        return None
    graph_hits = sum(
        1 for result, case in eligible if any(_skill_matches(record, case.expected_skill) for record in result.records[:3])
    )
    lexical_hits = 0
    for _, case in eligible:
        lexical = rank_records(case.query, records, strategy="hybrid")
        lexical_hits += int(any(_skill_matches(record, case.expected_skill) for record in lexical.records[:3]))
    return round((graph_hits - lexical_hits) / len(eligible), 4)


def _graph_diagnostics(
    records: list[SkillRecord],
    graph_cases: list[tuple[RankedResult, EvalCase]],
    edges: list[Any],
) -> dict[str, int]:
    lexical_cases = [
        (rank_records(case.query, records, strategy="hybrid", profile=case.profile), case)
        for _, case in graph_cases
    ]
    lexical_over_expansions = _graph_over_expansions(lexical_cases, edges)
    graph_over_expansions = _graph_over_expansions(graph_cases, edges)
    lift_cap_violations = 0
    candidate_leakage = 0
    confirmed = [edge for edge in edges if getattr(edge, "review_state", "") == "confirmed"]
    for (graph_result, case), (lexical_result, _) in zip(graph_cases, lexical_cases):
        lexical_scores = {record.id: score for score, record in lexical_result.scored_records}
        for score, record in graph_result.scored_records:
            baseline = lexical_scores.get(record.id)
            if baseline is not None and score > baseline * 1.08 + 1e-9:
                lift_cap_violations += 1
        confirmed_only = apply_graph_shadow(lexical_result, confirmed, records)
        if [record.id for record in graph_result.records[:3]] != [record.id for record in confirmed_only.records[:3]]:
            candidate_leakage += 1
    return {
        "over_expansion_delta": graph_over_expansions - lexical_over_expansions,
        "candidate_leakage_count": candidate_leakage,
        "lift_cap_violation_count": lift_cap_violations,
    }


def _format_rate(value: object) -> str:
    return "NA" if value is None else f"{float(value):.4f}"
