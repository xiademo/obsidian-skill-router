from __future__ import annotations

from dataclasses import replace
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable

from .classify import RISK_RANK, infer_action_risk
from .io_utils import GOVERNANCE_INPUT_MAX_BYTES, read_text_bounded
from .models import SkillRecord


ROUTING_FIELDS = {
    "package", "alternate_packages", "aliases", "positive_triggers",
    "negative_triggers", "route_mode", "trigger_mode", "default_routable",
    "requires_explicit_user_intent", "risk_level", "topic_risk", "action_risk",
    "side_effects", "permissions", "permissions_known", "network_access",
    "writes_files", "actions", "artifacts", "requires", "conflicts", "workflow_next",
    "related_candidates", "workflow_candidates", "duplicate_candidates", "conflict_candidates",
}
ENRICHMENT_FIELDS = {
    "aliases", "positive_triggers", "negative_triggers", "actions",
    "related_candidates", "workflow_candidates", "duplicate_candidates", "conflict_candidates",
}

ENRICHMENT_FIELD_ALIASES = {
    "capabilities": "actions",
    "positive_trigger": "positive_triggers",
    "negative_trigger": "negative_triggers",
    "related": "related_candidates",
    "related_candidate": "related_candidates",
    "workflow_candidate": "workflow_candidates",
    "duplicate_candidate": "duplicate_candidates",
    "conflict_candidate": "conflict_candidates",
}
PACKAGE_NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
ROUTE_MODES = {"default", "manual", "explicit-only", "profile-only"}
TRIGGER_MODES = {"default", "direct-or-semantic-match", "strong-semantic-match", "explicit-only", "profile-only", "manual"}
RISK_VALUES = {"low", "medium", "high", "unknown"}
TOPIC_RISK_VALUES = {"low-topic", "high-topic", "unknown"}
BOOLEAN_FIELDS = {"default_routable", "requires_explicit_user_intent", "permissions_known", "network_access", "writes_files"}
LIST_FIELDS = {
    "aliases", "positive_triggers", "negative_triggers", "actions", "artifacts", "side_effects", "permissions",
    "alternate_packages", "requires", "conflicts", "workflow_next", "related_candidates", "workflow_candidates",
    "duplicate_candidates", "conflict_candidates",
}


def load_routing_overlays(paths: Iterable[Path]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    overlays: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for path in paths:
        try:
            payload = json.loads(
                read_text_bounded(path, GOVERNANCE_INPUT_MAX_BYTES, label="Routing rules")
            )
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"Cannot read routing rules {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Routing rules must be an object: {path}")
        unknown_top = sorted(set(payload) - {"schema_version", "skills"})
        if unknown_top:
            raise ValueError(f"Unknown routing rules fields in {path}: {', '.join(unknown_top)}")
        if payload.get("schema_version") not in (None, "0.2"):
            raise ValueError(f"Unsupported routing rules schema_version in {path}: {payload.get('schema_version')!r}")
        items = payload.get("skills", [])
        if not isinstance(items, list):
            raise ValueError(f"Routing rules skills must be a list: {path}")
        for item in items:
            if not isinstance(item, dict):
                raise ValueError(f"Routing rule entries must be objects: {path}")
            allowed_fields = ROUTING_FIELDS | {
                "id", "skill_id", "root_id", "relative_path", "declared_name", "name",
                "category", "risk", "use_when",
            }
            unknown_fields = sorted(set(item) - allowed_fields)
            if unknown_fields:
                raise ValueError(f"Unknown routing rule fields in {path}: {', '.join(unknown_fields)}")
            target = _overlay_target(item, path)
            if target in overlays:
                raise ValueError(f"Duplicate routing rule target {target!r}: {path}")
            overlays[target] = dict(item)
    return overlays, warnings


def load_enrichment(paths: Iterable[Path]) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]], list[str]]:
    approved: dict[str, list[dict[str, Any]]] = {}
    candidates: list[dict[str, Any]] = []
    warnings: list[str] = []
    for path in paths:
        try:
            payload = json.loads(
                read_text_bounded(path, GOVERNANCE_INPUT_MAX_BYTES, label="Enrichment proposals")
            )
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"Cannot read enrichment proposals {path}: {exc}") from exc
        items = payload.get("proposals", payload if isinstance(payload, list) else [])
        if not isinstance(items, list):
            raise ValueError(f"Enrichment proposals must be a list: {path}")
        for raw in items:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            item["proposal_source"] = str(path)
            target_id = str(item.get("skill_id") or "").strip().lower()
            target_name = str(item.get("skill") or item.get("skill_name") or "").strip().lower()
            target = f"id:{target_id}" if target_id else target_name
            field = ENRICHMENT_FIELD_ALIASES.get(str(item.get("field") or "").strip(), str(item.get("field") or "").strip())
            item["field"] = field
            status = str(item.get("status") or "pending").strip().lower()
            if not target or not field:
                warnings.append(f"Incomplete enrichment proposal ignored: {path}")
                continue
            required = {"source_hash", "source_span", "confidence", "status", "value"}
            missing = sorted(key for key in required if key not in item or item.get(key) in (None, ""))
            try:
                confidence = float(item.get("confidence"))
            except (TypeError, ValueError):
                confidence = float("nan")
            if missing or not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
                item["status"] = "invalid"
                item["invalid_reason"] = "missing required fields" if missing else "confidence must be finite and between 0 and 1"
                candidates.append(item)
                warnings.append(f"Invalid enrichment proposal ignored for {target}: {field}")
                continue
            item["confidence"] = confidence
            if status == "approved" and field in ENRICHMENT_FIELDS:
                approved.setdefault(target, []).append(item)
                candidates.append(item)
            else:
                candidates.append(item)
    return approved, candidates, warnings


def _overlay_target(item: dict[str, Any], path: Path) -> str:
    skill_id = str(item.get("id") or item.get("skill_id") or "").strip().lower()
    root_id = str(item.get("root_id") or "").strip().lower()
    relative_path = str(item.get("relative_path") or "").strip().replace("\\", "/").strip("/")
    name = str(item.get("declared_name") or item.get("name") or "").strip().lower()
    selector_count = int(bool(skill_id)) + int(bool(root_id or relative_path)) + int(bool(name))
    if selector_count != 1 or bool(root_id) != bool(relative_path):
        raise ValueError(
            f"Routing rule must use exactly one selector: id, root_id+relative_path, or name: {path}"
        )
    if skill_id:
        return f"id:{skill_id}"
    if root_id:
        if relative_path.startswith("../") or "/../" in f"/{relative_path}/" or relative_path in {".", ".."}:
            raise ValueError(f"Unsafe routing rule relative_path: {relative_path!r}")
        return f"path:{root_id}:{relative_path}"
    return f"name:{name}"


def apply_registry_overrides(
    record: SkillRecord,
    overlay: dict[str, Any] | None,
    proposals: list[dict[str, Any]] | None,
) -> tuple[SkillRecord, list[str]]:
    updates: dict[str, Any] = {}
    provenance = dict(record.provenance)
    warnings: list[str] = []

    if overlay:
        mapping = {
            "category": "package",
            "risk": "risk_level",
            "use_when": "positive_triggers",
        }
        for source, target in mapping.items():
            value = overlay.get(source)
            if value not in (None, ""):
                if target == "positive_triggers":
                    if isinstance(value, str):
                        value = [value]
                    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
                        value = list(value)
                    else:
                        raise ValueError(
                            f"Routing overlay field {source} for {record.name} must be a string or list of strings"
                        )
                updates[target] = value
                provenance[target] = {"origin": "routing-overlay", "confidence": 1.0}
        for field in ROUTING_FIELDS:
            if field in overlay and overlay[field] not in (None, ""):
                updates[field] = overlay[field]
                provenance[field] = {"origin": "routing-overlay", "confidence": 1.0}

    for proposal in proposals or []:
        field = str(proposal.get("field"))
        status = str(proposal.get("status") or "").strip().lower()
        if status != "approved":
            warnings.append(f"Non-approved enrichment proposal ignored for {record.name}: {field}")
            continue
        try:
            confidence = float(proposal.get("confidence"))
        except (TypeError, ValueError):
            confidence = float("nan")
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0 or not str(proposal.get("source_span") or "").strip():
            warnings.append(f"Invalid approved enrichment proposal ignored for {record.name}: {field}")
            continue
        if str(proposal.get("source_hash") or "") != record.source_hash:
            proposal["status"] = "stale"
            proposal["stale_reason"] = "source hash mismatch"
            warnings.append(f"Stale enrichment proposal ignored for {record.name}: {field}")
            continue
        if field not in ENRICHMENT_FIELDS:
            warnings.append(f"Protected enrichment field ignored for {record.name}: {field}")
            continue
        value = proposal.get("value")
        updates[field] = value
        provenance[field] = {
            "origin": "approved-enrichment",
            "confidence": confidence,
            "source_span": str(proposal.get("source_span", "")),
        }

    _validate_overlay_types(record.name, updates)
    for field in LIST_FIELDS:
        if field in updates:
            updates[field] = [str(value) for value in updates[field]]
    package = str(updates.get("package", record.package) or "")
    alternate_packages = [str(value) for value in updates.get("alternate_packages", record.alternate_packages)]
    for value in [package, *alternate_packages]:
        if value and (len(value) > 64 or not PACKAGE_NAME_RE.fullmatch(value)):
            raise ValueError(f"Unsafe routing package name for {record.name}: {value!r}")
    risk = str(updates.get("risk_level", record.risk_level))
    action_text = " ".join(
        str(value).lower()
        for field in ("actions", "side_effects", "permissions")
        for value in updates.get(field, getattr(record, field))
    )
    inferred_action, _ = infer_action_risk(action_text)
    if (
        updates.get("network_access", record.network_access) is True
        or updates.get("writes_files", record.writes_files) is True
    ) and RISK_RANK[inferred_action] < RISK_RANK["medium"]:
        inferred_action = "medium"
    declared_action = str(updates.get("action_risk", record.action_risk))
    effective_action = max((declared_action, inferred_action), key=lambda value: RISK_RANK.get(value, 0))
    if effective_action != declared_action:
        updates["action_risk"] = effective_action
        provenance["action_risk"] = {
            "origin": "inferred", "confidence": 0.8, "evidence": "explicit action/permission/side-effect"
        }
    if RISK_RANK.get(effective_action, 0) > RISK_RANK.get(risk, 0):
        updates["risk_level"] = effective_action
        updates["action_risk"] = effective_action
        provenance["risk_level"] = {
            "origin": "inferred",
            "confidence": 0.8 if effective_action == "high" else 0.7,
            "evidence": "explicit action/permission/side-effect overrides a lower declared risk",
        }
    updates["provenance"] = provenance
    return replace(record, **updates), warnings


def normalize_source_policy(record: SkillRecord) -> tuple[SkillRecord, list[str]]:
    """Normalize untrusted Skill policy metadata without failing open."""
    updates: dict[str, Any] = {}
    warnings: list[str] = []
    if record.route_mode not in ROUTE_MODES:
        warnings.append(f"Unknown source route_mode for {record.name}: {record.route_mode}; normalized to manual")
        updates["route_mode"] = "manual"
    if record.trigger_mode not in TRIGGER_MODES:
        warnings.append(f"Unknown source trigger_mode for {record.name}: {record.trigger_mode}; normalized to manual")
        updates["trigger_mode"] = "manual"
    if updates:
        updates["default_routable"] = False
        updates["requires_explicit_user_intent"] = True
    return (replace(record, **updates) if updates else record), warnings


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _validate_overlay_types(skill_name: str, updates: dict[str, Any]) -> None:
    for field in BOOLEAN_FIELDS:
        if field in updates and not isinstance(updates[field], bool):
            raise ValueError(f"Routing overlay field {field} for {skill_name} must be boolean")
    for field in LIST_FIELDS:
        if field not in updates:
            continue
        value = updates[field]
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError(f"Routing overlay field {field} for {skill_name} must be a list of strings")
    route_mode = str(updates.get("route_mode", ""))
    if route_mode and route_mode not in ROUTE_MODES:
        raise ValueError(f"Unknown route_mode for {skill_name}: {route_mode}")
    trigger_mode = str(updates.get("trigger_mode", ""))
    if trigger_mode and trigger_mode not in TRIGGER_MODES:
        raise ValueError(f"Unknown trigger_mode for {skill_name}: {trigger_mode}")
    for field in ("risk_level", "action_risk"):
        value = str(updates.get(field, ""))
        if value and value not in RISK_VALUES:
            raise ValueError(f"Unknown {field} for {skill_name}: {value}")
    topic_risk = str(updates.get("topic_risk", ""))
    if topic_risk and topic_risk not in TOPIC_RISK_VALUES:
        raise ValueError(f"Unknown topic_risk for {skill_name}: {topic_risk}")
