from __future__ import annotations

import re
from dataclasses import replace

from .config import DEFAULT_PACKAGE_RULES, DEFAULT_RISK_KEYWORDS, merge_rules
from .models import PACKAGES, RISK_LEVELS, SkillRecord


HIGH_ACTIONS = (
    "delete", "remove-item", "rm -rf", "deploy", "publish", "install",
    "execute", "run command", "payment", "complete payment", "send token",
    "send secret", "read credential", "write credential", "oauth",
)
MEDIUM_ACTIONS = (
    "write", "modify", "upload", "download", "login", "browser automation",
    "external api", "api call", "network", "database", "filesystem",
)
RISK_RANK = {"unknown": 0, "low": 1, "medium": 2, "high": 3}


def classify_skill(
    record: SkillRecord,
    package_rules: dict[str, list[str]] | None = None,
    risk_keywords: dict[str, list[str]] | None = None,
    package_hint: str = "",
) -> SkillRecord:
    rules = merge_rules(package_rules, DEFAULT_PACKAGE_RULES)
    risk_rules = merge_rules(risk_keywords, DEFAULT_RISK_KEYWORDS)
    text = " ".join(
        [
            record.name,
            record.description,
            record.when_to_use,
            " ".join(record.input_types),
            " ".join(record.output_types),
            package_hint,
        ]
    ).lower()

    package, confidence, alternates = _classify_package(text, rules, package_hint)
    action_text = " ".join([*record.actions, *record.side_effects, *record.permissions]).lower()
    risk_level, risk_reasons = assess_risk_level(record.risk_level, text, risk_rules, package, action_text=action_text)
    topic_risk = "high-topic" if _keyword_hits(text, ["credential", "secret", "token", "api key", "payment", "production"]) else "low-topic"
    action_risk = risk_level if risk_level != "unknown" else "unknown"
    warnings = list(record.warnings)
    if not package:
        warnings.append("No package classification above threshold")
    if confidence < 0.25:
        warnings.append("Low classification confidence")
    for field in ["description", "when_to_use"]:
        if not getattr(record, field):
            warnings.append(f"Missing {field}")
    if not record.input_types:
        warnings.append("Missing input_types")
    if not record.output_types:
        warnings.append("Missing output_types")
    if risk_level == "unknown":
        warnings.append("Unknown risk level")

    return replace(
        record,
        package=package,
        confidence=confidence,
        alternate_packages=alternates,
        risk_level=risk_level,
        topic_risk=topic_risk,
        action_risk=action_risk,
        provenance={
            **record.provenance,
            "package": {
                "origin": "manifest" if package_hint.strip().lower() in PACKAGES else "inferred",
                "confidence": confidence,
                "evidence": package_hint if package_hint else "deterministic package keyword rules",
            },
            "risk_level": {
                "origin": "explicit" if record.risk_level in RISK_LEVELS - {"unknown"} else "action-inference",
                "confidence": 1.0 if record.risk_level in RISK_LEVELS - {"unknown"} else (0.8 if risk_reasons else 0.0),
                "evidence": risk_reasons,
            },
        },
        warnings=sorted(set(warnings)),
    )


def _classify_package(
    text: str,
    rules: dict[str, list[str]],
    package_hint: str,
) -> tuple[str, float, list[str]]:
    hint = package_hint.strip().lower()
    if hint in PACKAGES:
        return hint, 1.0, []

    scored: list[tuple[str, int]] = []
    for package in PACKAGES:
        score = 0
        for keyword in rules.get(package, []):
            pattern = re.escape(keyword.lower())
            if re.search(rf"\b{pattern}\b", text) or keyword.lower() in text:
                score += 1
        scored.append((package, score))
    scored.sort(key=lambda item: (-item[1], item[0]))
    best, score = scored[0]
    if score == 0:
        return "", 0.0, []
    confidence = min(0.95, 0.2 + score * 0.15)
    alternates = [package for package, alt_score in scored[1:4] if alt_score > 0]
    return best, round(confidence, 2), alternates


def assess_risk_level(
    existing: str,
    text: str,
    risk_rules: dict[str, list[str]],
    package: str = "",
    *,
    action_text: str = "",
) -> tuple[str, list[str]]:
    normalized = existing.strip().lower()
    action_level, action_reasons = infer_action_risk(action_text, risk_rules)
    if normalized in RISK_LEVELS and normalized != "unknown":
        if RISK_RANK[action_level] > RISK_RANK[normalized]:
            return action_level, [f"explicit risk metadata: {normalized}", *action_reasons]
        return normalized, [f"explicit risk metadata: {normalized}"]
    if action_level != "unknown":
        return action_level, action_reasons
    return "unknown", ["no risk signal matched"]


def infer_action_risk(action_text: str, risk_rules: dict[str, list[str]] | None = None) -> tuple[str, list[str]]:
    normalized_action = " ".join(action_text.lower().split())
    sensitive_transfer = re.search(
        r"\b(?:send|transmit|transfer|upload|exfiltrate|copy|export|share|forward)\b"
        r"(?:\s+\w+){0,4}\s+"
        r"(?:credentials?|secrets?|tokens?|api\s+keys?|private\s+keys?)\b",
        normalized_action,
    )
    if sensitive_transfer:
        return "high", [f"high action: sensitive data transfer ({sensitive_transfer.group(0)})"]
    for level, keywords in (("high", HIGH_ACTIONS), ("medium", MEDIUM_ACTIONS)):
        hits = _keyword_hits(action_text, list(keywords))
        if hits:
            return level, [f"{level} action: {hit}" for hit in hits[:5]]
    configured = risk_rules or {}
    for level in ("high", "medium"):
        known_actions = set(HIGH_ACTIONS) | set(MEDIUM_ACTIONS)
        hits = [
            hit for hit in _keyword_hits(action_text, configured.get(level, []))
            if " " in hit or hit in known_actions
        ]
        if hits:
            return level, [f"{level} configured action: {hit}" for hit in hits[:5]]
    return "unknown", []


def _keyword_hits(text: str, keywords: list[str]) -> list[str]:
    hits: list[str] = []
    for keyword in keywords:
        lowered = keyword.lower()
        if re.search(r"[^a-z0-9]", lowered):
            if lowered in text:
                hits.append(keyword)
        elif re.search(rf"\b{re.escape(lowered)}\b", text):
            hits.append(keyword)
    return hits


def _package_risk_default(package: str) -> tuple[str, str] | None:
    if package in {"automation-ops", "memory-knowledge", "safety-governance"}:
        return "medium", f"package default: {package} can involve external systems, writes, or governance decisions"
    if package in {"document-processing", "career-resume", "coding-workflow", "research-writing"}:
        return "low", f"package default: {package} is usually local, read-oriented, or authoring-focused without stronger signals"
    return None
