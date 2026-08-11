from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import os
import re
import stat
from pathlib import Path
from typing import Any

from .classify import classify_skill
from .extract import extract_skill_metadata
from .io_utils import SKILL_METADATA_MAX_BYTES, read_bytes_bounded
from .models import SkillRecord
from .registry import apply_registry_overrides, normalize_source_policy


DETECT_FILES = ["SKILL.md", "manifest.json", "README.md"]
RESOURCE_DIRS = {"references", "scripts", "assets", ".agents", ".git", "node_modules", "test_artifacts", "__pycache__"}


def scan_roots(
    roots: list[Path],
    package_rules: dict[str, list[str]] | None = None,
    risk_keywords: dict[str, list[str]] | None = None,
    *,
    routing_overlays: dict[str, dict[str, Any]] | None = None,
    enrichment: dict[str, list[dict[str, Any]]] | None = None,
    legacy_readme_skills: bool = False,
    max_depth: int = 6,
) -> tuple[list[SkillRecord], list[str]]:
    warnings: list[str] = []
    records: list[SkillRecord] = []
    seen_dirs: set[Path] = set()
    overlay_name_matches: dict[str, str] = {}
    enrichment_name_matches: dict[str, str] = {}
    matched_overlay_targets: set[str] = set()
    overlay_target_matches: dict[str, str] = {}
    scanned_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    root_ids = _root_ids(roots)

    for root_index, root in enumerate(roots):
        if not root.exists():
            raise ValueError(f"Skills root does not exist: {root}")
        if not root.is_dir():
            raise ValueError(f"Skills root is not a directory: {root}")
        scan_root = root.resolve()
        if _is_agents_skills_path(scan_root):
            raise ValueError(f"Refusing to scan .agents/skills: {root}")
        root_id = root_ids[root_index]
        for skill_dir, detected, discovery_warnings in _find_skill_dirs(scan_root, legacy_readme_skills, max_depth):
            warnings.extend(discovery_warnings)
            oversized = [
                name for name in detected
                if (skill_dir / name).stat().st_size > SKILL_METADATA_MAX_BYTES
            ]
            if oversized:
                warnings.append(
                    f"Skill metadata exceeds {SKILL_METADATA_MAX_BYTES} bytes and was skipped: "
                    f"{skill_dir} ({', '.join(oversized)})"
                )
                continue
            resolved = skill_dir.resolve()
            if resolved in seen_dirs:
                continue
            seen_dirs.add(resolved)
            metadata, metadata_warnings = extract_skill_metadata(skill_dir, detected)
            warnings.extend(f"{skill_dir}: {warning}" for warning in metadata_warnings)
            if "manifest.json" in detected and "SKILL.md" not in detected:
                if not metadata.get("name") or not metadata.get("description"):
                    warnings.append(f"Manifest-only skill missing name/description and was skipped: {skill_dir}")
                    continue
                warnings.append(f"Deprecated manifest-only skill discovered: {skill_dir}")

            relative = skill_dir.relative_to(scan_root).as_posix() or "."
            source_hash, source_mtime = _source_fingerprint(skill_dir, detected)
            name = str(metadata.get("name") or skill_dir.name)
            provenance = {
                "name": {"origin": "skill-frontmatter" if "SKILL.md" in detected else "manifest", "confidence": 1.0},
                "description": {"origin": "skill-frontmatter" if "SKILL.md" in detected else "manifest", "confidence": 1.0},
            }
            record = SkillRecord(
                id=_skill_id(name, root_id, relative),
                name=name,
                source_path=str(resolved),
                detected_files=detected,
                description=str(metadata.get("description", "")),
                when_to_use=str(metadata.get("when_to_use", "")),
                input_types=list(metadata.get("input_types", [])),
                output_types=list(metadata.get("output_types", [])),
                risk_level=str(metadata.get("risk_level", "unknown")),
                warnings=metadata_warnings,
                source_root_id=root_id,
                source_relative_path=relative,
                source_hash=source_hash,
                source_mtime=source_mtime,
                last_scanned=scanned_at,
                compatibility=str(metadata.get("compatibility", "")),
                allowed_tools=list(metadata.get("allowed_tools", [])),
                aliases=list(metadata.get("aliases", [])),
                positive_triggers=list(metadata.get("positive_triggers", [])),
                negative_triggers=list(metadata.get("negative_triggers", [])),
                actions=list(metadata.get("actions", [])),
                artifacts=list(metadata.get("artifacts", [])),
                requires=list(metadata.get("requires", [])),
                conflicts=list(metadata.get("conflicts", [])),
                workflow_next=list(metadata.get("workflow_next", [])),
                route_mode=str(metadata.get("route_mode", "default")),
                trigger_mode=str(metadata.get("trigger_mode", "direct-or-semantic-match")),
                side_effects=list(metadata.get("side_effects", [])),
                permissions=list(metadata.get("permissions", [])),
                permissions_known="permissions" in metadata,
                network_access=metadata.get("network_access"),
                writes_files=metadata.get("writes_files"),
                default_routable=bool(metadata.get("default_routable", True)),
                requires_explicit_user_intent=bool(metadata.get("requires_explicit_user_intent", False)),
                status=str(metadata.get("status", "unknown")),
                provider=str(metadata.get("provider", "user")),
                provenance=provenance,
            )
            classified = classify_skill(
                record,
                package_rules=package_rules,
                risk_keywords=risk_keywords,
                package_hint=str(metadata.get("package_hint", "")),
            )
            classified, source_policy_warnings = normalize_source_policy(classified)
            warnings.extend(source_policy_warnings)
            normalized_name = classified.name.lower()
            name_overlay = (
                (routing_overlays or {}).get(f"name:{normalized_name}")
                or (routing_overlays or {}).get(normalized_name)
            )
            if name_overlay:
                previous = overlay_name_matches.setdefault(normalized_name, classified.id)
                if previous != classified.id:
                    raise ValueError(
                        f"Ambiguous name-targeted routing rule for {classified.name!r}; "
                        "use id or root_id+relative_path"
                    )
            name_proposals = (enrichment or {}).get(normalized_name, [])
            if name_proposals:
                previous = enrichment_name_matches.setdefault(normalized_name, classified.id)
                if previous != classified.id:
                    raise ValueError(
                        f"Ambiguous name-targeted enrichment for {classified.name!r}; use skill_id"
                    )
            overlay = None
            for target in (
                f"id:{classified.id.lower()}",
                f"path:{classified.source_root_id.lower()}:{classified.source_relative_path}",
                f"name:{normalized_name}",
                normalized_name,
            ):
                if target in (routing_overlays or {}):
                    previous = overlay_target_matches.setdefault(target, classified.id)
                    if previous != classified.id:
                        raise ValueError(
                            f"Routing rule target matched multiple Skills: {target}; use a stable skill id"
                        )
                    overlay = (routing_overlays or {})[target]
                    matched_overlay_targets.add(target)
                    break
            proposals = [
                *(enrichment or {}).get(f"id:{classified.id.lower()}", []),
                *name_proposals,
            ]
            classified, override_warnings = apply_registry_overrides(classified, overlay, proposals)
            warnings.extend(override_warnings)
            records.append(
                replace(
                    classified,
                    warnings=sorted(set([*classified.warnings, *source_policy_warnings, *override_warnings])),
                )
            )

    unmatched_overlays = sorted(set(routing_overlays or {}) - matched_overlay_targets)
    if unmatched_overlays:
        raise ValueError(f"Routing rule targets did not match any Skill: {', '.join(unmatched_overlays)}")
    records.sort(key=lambda item: (item.package or "zz-unclassified", item.name.lower(), item.source_relative_path.lower()))
    return records, sorted(set(warnings))


def _find_skill_dirs(root: Path, legacy_readme_skills: bool, max_depth: int) -> list[tuple[Path, list[str], list[str]]]:
    found: list[tuple[Path, list[str], list[str]]] = []
    for directory_text, directory_names, _ in os.walk(root, topdown=True, followlinks=False):
        directory = Path(directory_text)
        relative = directory.relative_to(root)
        depth = len(relative.parts)
        directory_names[:] = sorted(
            (
                name for name in directory_names
                if name.lower() not in RESOURCE_DIRS
                and not _is_linklike(directory / name)
                and (directory / name).resolve(strict=False).is_relative_to(root)
                and not _is_agents_skills_path((directory / name).resolve(strict=False))
            ),
            key=str.lower,
        ) if depth < max_depth else []
        if depth > max_depth or _is_agents_skills_path(directory.resolve(strict=False)):
            continue
        has_skill = _regular_file(directory / "SKILL.md")
        has_manifest = _regular_file(directory / "manifest.json")
        has_readme = _regular_file(directory / "README.md")
        if not has_skill and not has_manifest and not (legacy_readme_skills and has_readme):
            continue
        detected = [name for name in DETECT_FILES if _regular_file(directory / name)]
        messages = []
        if legacy_readme_skills and has_readme and not has_skill and not has_manifest:
            messages.append(f"Legacy README-only skill discovered: {directory}")
        found.append((directory, detected, messages))
    return found


def _regular_file(path: Path) -> bool:
    return path.is_file() and not _is_linklike(path)


def _is_linklike(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _is_agents_skills_path(path: Path) -> bool:
    parts = [part.lower() for part in path.parts]
    return any(parts[index:index + 2] == [".agents", "skills"] for index in range(max(0, len(parts) - 1)))


def _source_fingerprint(skill_dir: Path, detected: list[str]) -> tuple[str, str]:
    digest = hashlib.sha256()
    mtimes: list[float] = []
    for filename in sorted(name for name in detected if name in {"SKILL.md", "manifest.json", "README.md"}):
        path = skill_dir / filename
        data = read_bytes_bounded(path, SKILL_METADATA_MAX_BYTES, label="Skill metadata")
        digest.update(filename.encode("utf-8"))
        digest.update(b"\0")
        digest.update(data)
        mtimes.append(path.stat().st_mtime)
    modified = datetime.fromtimestamp(max(mtimes), timezone.utc).isoformat(timespec="seconds") if mtimes else ""
    return digest.hexdigest(), modified


def _root_ids(roots: list[Path]) -> list[str]:
    seen: dict[str, int] = {}
    result: list[str] = []
    for root in roots:
        base = re.sub(r"[^a-z0-9]+", "-", root.name.lower()).strip("-") or "skills"
        seen[base] = seen.get(base, 0) + 1
        result.append(base if seen[base] == 1 else f"{base}-{seen[base]}")
    return result


def _skill_id(name: str, root_id: str, relative_path: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "skill"
    stable_source = f"{root_id}:{relative_path}"
    digest = hashlib.sha256(stable_source.encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}"
