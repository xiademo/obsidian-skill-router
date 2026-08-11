from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


RISK_LEVELS = {"low", "medium", "high", "unknown"}
GRAPH_RELATIONS = {
    "related",
    "workflow_next",
    "complements",
    "fallback_to",
    "duplicates",
    "conflicts_with",
    "governs",
    "requires",
}
PACKAGES = [
    "document-processing",
    "career-resume",
    "coding-workflow",
    "research-writing",
    "automation-ops",
    "memory-knowledge",
    "safety-governance",
]


@dataclass(frozen=True)
class SourceIdentity:
    root_id: str = ""
    relative_path: str = ""
    content_hash: str = ""
    modified_at: str = ""
    scanned_at: str = ""


@dataclass(frozen=True)
class RoutingPolicy:
    aliases: list[str] = field(default_factory=list)
    positive_triggers: list[str] = field(default_factory=list)
    negative_triggers: list[str] = field(default_factory=list)
    route_mode: str = "default"
    trigger_mode: str = "direct-or-semantic-match"
    default_routable: bool = True
    requires_explicit_user_intent: bool = False


@dataclass(frozen=True)
class RiskPolicy:
    topic_risk: str = "unknown"
    action_risk: str = "unknown"
    side_effects: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    permissions_known: bool = False
    network_access: bool | None = None
    writes_files: bool | None = None


@dataclass(frozen=True)
class SkillRecord:
    id: str
    name: str
    source_path: str
    detected_files: list[str]
    description: str = ""
    when_to_use: str = ""
    input_types: list[str] = field(default_factory=list)
    output_types: list[str] = field(default_factory=list)
    risk_level: str = "unknown"
    package: str = ""
    confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)
    alternate_packages: list[str] = field(default_factory=list)
    source_root_id: str = ""
    source_relative_path: str = ""
    source_hash: str = ""
    source_mtime: str = ""
    last_scanned: str = ""
    provider: str = "user"
    compatibility: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    positive_triggers: list[str] = field(default_factory=list)
    negative_triggers: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    workflow_next: list[str] = field(default_factory=list)
    related_candidates: list[str] = field(default_factory=list)
    workflow_candidates: list[str] = field(default_factory=list)
    duplicate_candidates: list[str] = field(default_factory=list)
    conflict_candidates: list[str] = field(default_factory=list)
    route_mode: str = "default"
    trigger_mode: str = "direct-or-semantic-match"
    default_routable: bool = True
    requires_explicit_user_intent: bool = False
    topic_risk: str = "unknown"
    action_risk: str = "unknown"
    side_effects: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    permissions_known: bool = False
    network_access: bool | None = None
    writes_files: bool | None = None
    status: str = "unknown"
    review_state: str = "confirmed"
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PackageRecord:
    name: str
    skills: list[SkillRecord]


@dataclass(frozen=True)
class GraphEdge:
    source: str
    target: str
    relation: str
    reason: str
    confidence: float = 1.0
    origin: str = "inferred"
    review_state: str = "confirmed"
    evidence: str = ""
    rule_version: str = "0.2"


@dataclass(frozen=True)
class ScanReport:
    scanned_roots: list[str]
    skills_found: int
    classified: int
    unclassified: int
    missing_metadata: int
    warnings: list[str]


@dataclass(frozen=True)
class RiskReport:
    high_risk: list[SkillRecord]
    unknown_risk: list[SkillRecord]


@dataclass(frozen=True)
class QualityReport:
    duplicate_names: dict[str, list[str]]
    duplicate_paths: list[str]
    low_confidence: list[SkillRecord]
    needs_review: list[SkillRecord]


def vault_dirs(vault: Path) -> list[Path]:
    dirs = [
        vault / "00_Router",
        vault / "01_Packages",
        vault / "02_Workflows",
        vault / "03_Graph",
        vault / "04_Inbox",
        vault / "04_Inbox" / "unclassified",
        vault / "04_Inbox" / "needs-review",
        vault / "04_Inbox" / "candidates",
        vault / "05_Reports",
    ]
    dirs.extend(vault / "01_Packages" / package / "skills" for package in PACKAGES)
    return dirs
