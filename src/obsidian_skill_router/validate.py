from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any

from .models import GRAPH_RELATIONS, PACKAGES, vault_dirs


SKILL_FIELDS = {
    "id", "name", "package", "description", "when_to_use", "input_types", "output_types", "risk_level",
    "confidence", "alternate_packages", "source_root_id", "source_relative_path", "source_hash", "source_mtime",
    "last_scanned", "provider", "compatibility", "allowed_tools", "aliases", "positive_triggers",
    "negative_triggers", "actions", "artifacts", "requires", "conflicts", "workflow_next", "related_candidates",
    "workflow_candidates", "duplicate_candidates", "conflict_candidates", "route_mode", "trigger_mode", "default_routable",
    "requires_explicit_user_intent", "topic_risk", "action_risk", "side_effects", "permissions",
    "permissions_known", "network_access", "writes_files", "status", "review_state", "provenance", "card_path",
}
EDGE_FIELDS = {"source", "target", "relation", "reason", "confidence", "origin", "review_state", "evidence", "rule_version"}
TOP_FIELDS = {
    "schema_version", "compiler_version", "rules_version", "generated_at", "source_roots", "packages",
    "managed_files_manifest", "index_stats", "skills", "edges",
}
SAFE_PACKAGE_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
VAULT_ROOTS = {
    ".obsidian-skill-router", "00_Router", "01_Packages", "02_Workflows",
    "03_Graph", "04_Inbox", "05_Reports",
}
CARD_ROOTS = {"01_Packages", "04_Inbox"}
CANDIDATE_EDGE_ORIGINS = {"approved-enrichment", "heuristic", "mutual-knn", "name-similarity"}


def safe_vault_path(vault: Path, value: str, *, allowed_roots: set[str] | None = None) -> Path:
    """Resolve a generated-Vault path without following symlinks or junctions."""
    return _safe_vault_join(vault.resolve(), value, allowed_roots=allowed_roots)


def validate_vault(vault: Path, *, strict: bool = False, check_sources: bool = False) -> tuple[bool, list[str]]:
    vault = vault.resolve()
    errors: list[str] = []
    try:
        index_path = _safe_vault_join(vault, "00_Router/routing-index.json")
    except ValueError as exc:
        return False, [str(exc)]
    payload: dict[str, Any] = {}
    schema = ""
    if index_path.is_file():
        try:
            loaded = json.loads(index_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
                schema = str(payload.get("schema_version", ""))
            else:
                errors.append(f"Malformed routing index object: {index_path}")
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"Malformed routing index {index_path}: {exc}")
    else:
        errors.append(f"Missing file: {index_path}")

    if schema not in {"0.1", "0.2"}:
        errors.append(f"Malformed routing index schema: {index_path}")
    elif strict and schema == "0.1":
        errors.append("routing-index 0.1 is not accepted by --strict; rescan to migrate to 0.2")
    try:
        managed_manifest = _safe_vault_join(vault, ".obsidian-skill-router/managed-files.json")
        if managed_manifest.is_file() and schema == "0.1":
            errors.append("Managed Registry schema downgrade detected")
    except ValueError as exc:
        errors.append(str(exc))

    for directory in vault_dirs(vault):
        if _is_linklike(directory):
            errors.append(f"Vault directory cannot be a symlink or junction: {directory}")
        elif not directory.is_dir():
            errors.append(f"Missing directory: {directory}")

    required_names = [
        "00_Router/Skill Package Map.md", "00_Router/Global Skill Search Index.md",
        "00_Router/Cross-Package Graph.md", "03_Graph/skill_edges.csv",
        "03_Graph/package_edges.csv", "05_Reports/scan-report.md",
        "05_Reports/risk-report.md", "05_Reports/quality-report.md",
        "05_Reports/calibration-report.md", "05_Reports/duplicate-conflict-report.md",
        "05_Reports/graph-quality-report.md",
    ]
    required_names.extend(f"01_Packages/{package}/Package Index.md" for package in PACKAGES)
    if schema == "0.2":
        required_names.extend(["03_Graph/skill_edges.audit.csv", ".obsidian-skill-router/managed-files.json"])
    required: list[Path] = []
    for name in required_names:
        try:
            required.append(_safe_vault_join(vault, name))
        except ValueError as exc:
            errors.append(str(exc))
    for path in required:
        if not path.is_file():
            errors.append(f"Missing file: {path}")

    csv_names = [
        ("03_Graph/skill_edges.csv", ["source", "target", "relation", "reason", "confidence"]),
        ("03_Graph/package_edges.csv", ["source_package", "target_package", "relation", "reason"]),
    ]
    if schema == "0.2":
        csv_names.append(("03_Graph/skill_edges.audit.csv", ["source", "target", "relation", "reason", "confidence", "origin", "review_state", "evidence", "rule_version"]))
    csv_specs: list[tuple[Path, list[str]]] = []
    for name, expected in csv_names:
        try:
            csv_specs.append((_safe_vault_join(vault, name), expected))
        except ValueError as exc:
            errors.append(str(exc))
    for path, expected in csv_specs:
        if path.is_file():
            errors.extend(_validate_csv(path, expected))

    if payload:
        errors.extend(_validate_payload(vault, payload, strict=strict, check_sources=check_sources))
    if schema == "0.2":
        errors.extend(_validate_managed(vault, strict=strict))
    errors.extend(_validate_wikilinks(vault))
    return not errors, errors


def _validate_csv(path: Path, expected: list[str]) -> list[str]:
    try:
        with path.open("r", newline="", encoding="utf-8") as handle:
            headers = next(csv.reader(handle), None)
        return [] if headers == expected else [f"Malformed CSV header in {path}: {headers}"]
    except (OSError, csv.Error) as exc:
        return [f"Malformed CSV {path}: {exc}"]


def _validate_payload(vault: Path, payload: dict[str, Any], *, strict: bool, check_sources: bool) -> list[str]:
    errors: list[str] = []
    if strict and payload.get("schema_version") == "0.2":
        try:
            # Local import avoids a module cycle: routing imports the canonical
            # field sets above, while strict validation must use the exact same
            # fail-closed decoder as route/hook.
            from .routing import _decode_routing_payload

            _decode_routing_payload(vault, payload)
        except (TypeError, ValueError) as exc:
            errors.append(f"Routing schema validation failed: {exc}")
    if strict and set(payload) - TOP_FIELDS:
        errors.append(f"Unknown routing index fields: {', '.join(sorted(set(payload) - TOP_FIELDS))}")
    skills = payload.get("skills")
    if not isinstance(skills, list):
        return ["Malformed routing index skills"]
    ids: set[str] = set()
    roots: dict[str, Path] = {}
    source_roots = payload.get("source_roots", [])
    if not isinstance(source_roots, list):
        errors.append("Malformed source_roots mapping")
        source_roots = []
    for item in source_roots:
        if not isinstance(item, dict) or not item.get("id") or not isinstance(item.get("path"), str):
            errors.append("Malformed source root entry")
            continue
        raw_path = str(item["path"])
        if _is_network_or_device_path(raw_path):
            errors.append(f"Network or device source root is not allowed: {raw_path!r}")
            continue
        roots[str(item["id"])] = Path(raw_path)
    for item in skills:
        if not isinstance(item, dict) or not item.get("id") or not item.get("name") or not item.get("card_path"):
            errors.append("Malformed routing index skill entry")
            continue
        skill_id = str(item["id"])
        if skill_id in ids:
            errors.append(f"Duplicate skill id: {skill_id}")
        ids.add(skill_id)
        if strict:
            unknown = set(item) - SKILL_FIELDS
            if unknown:
                errors.append(f"Unknown skill fields for {skill_id}: {', '.join(sorted(unknown))}")
            package = str(item.get("package", ""))
            if package and (len(package) > 64 or not SAFE_PACKAGE_RE.fullmatch(package)):
                errors.append(f"Unsafe package name for {skill_id}: {package!r}")
        try:
            card = _safe_vault_join(vault, str(item["card_path"]), allowed_roots=CARD_ROOTS)
        except ValueError as exc:
            errors.append(f"Unsafe skill card path for {skill_id}: {exc}")
            card = None
        if card is not None and not card.is_file():
            errors.append(f"Missing skill card for {skill_id}: {card}")
        if check_sources and item.get("source_hash"):
            root = roots.get(str(item.get("source_root_id", "")))
            if root is None:
                errors.append(f"Missing source root mapping for {skill_id}")
            else:
                try:
                    source = _safe_source_join(root, str(item.get("source_relative_path", ".")))
                except ValueError as exc:
                    errors.append(f"Unsafe source path for {skill_id}: {exc}")
                else:
                    actual = _source_hash(source)
                    if actual != str(item.get("source_hash")):
                        errors.append(f"Source hash drift for {skill_id}: {source}")
    for edge in payload.get("edges", []):
        if not isinstance(edge, dict):
            errors.append("Malformed routing index edge")
            continue
        if strict and set(edge) - EDGE_FIELDS:
            errors.append(f"Unknown edge fields: {', '.join(sorted(set(edge) - EDGE_FIELDS))}")
        relation = str(edge.get("relation", ""))
        if relation not in GRAPH_RELATIONS:
            errors.append(f"Invalid edge relation: {relation}")
        source, target = str(edge.get("source", "")), str(edge.get("target", ""))
        source_valid = _valid_edge_endpoint(source, ids)
        target_valid = _valid_edge_endpoint(target, ids)
        if not source_valid or not target_valid:
            errors.append(f"Dangling skill edge: {source} -> {target}")
        state = str(edge.get("review_state", ""))
        if strict and state not in {"confirmed", "candidate", "rejected"}:
            errors.append(f"Invalid review state: {state}")
        if strict and state == "confirmed" and str(edge.get("origin", "")) in CANDIDATE_EDGE_ORIGINS | {"candidate"}:
            errors.append(f"Candidate edge marked confirmed: {source} -> {target}")
        if not edge.get("origin") or not edge.get("evidence") or not edge.get("rule_version"):
            errors.append(f"Edge lacks audit metadata: {source} -> {target}")
    return errors


def _valid_edge_endpoint(value: str, ids: set[str]) -> bool:
    if value in ids:
        return True
    prefix, separator, name = value.partition(":")
    return bool(separator and prefix in {"package", "inbox"} and SAFE_PACKAGE_RE.fullmatch(name))


def _source_hash(path: Path) -> str:
    digest = hashlib.sha256()
    if not path.is_dir():
        return ""
    for name in ("README.md", "SKILL.md", "manifest.json"):
        item = path / name
        if item.is_file() and not _is_linklike(item) and item.stat().st_size <= 4 * 1024 * 1024:
            digest.update(name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(item.read_bytes())
    return digest.hexdigest()


def _validate_managed(vault: Path, *, strict: bool) -> list[str]:
    try:
        path = _safe_vault_join(vault, ".obsidian-skill-router/managed-files.json")
    except ValueError as exc:
        return [str(exc)]
    if not path.is_file():
        return []
    try:
        if path.stat().st_size > 16 * 1024 * 1024:
            return [f"Managed-files manifest exceeds 16777216 bytes: {path}"]
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [f"Malformed managed-files manifest: {exc}"]
    errors: list[str] = []
    if not isinstance(payload, dict) or payload.get("schema_version") != "0.2":
        return ["Malformed managed-files manifest: expected schema_version 0.2 object"]
    files = payload.get("files")
    if not isinstance(files, list):
        return ["Malformed managed-files manifest: files must be a list"]
    seen: set[str] = set()
    index_entries = 0
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            errors.append("Malformed managed file entry")
            continue
        relative = item.get("path")
        digest = item.get("sha256")
        if not isinstance(relative, str) or not relative or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            errors.append("Malformed managed file path or SHA-256")
            continue
        if relative in seen:
            errors.append(f"Duplicate managed file entry: {relative}")
            continue
        seen.add(relative)
        if relative == "00_Router/routing-index.json":
            index_entries += 1
        try:
            managed = _safe_vault_join(vault, relative, allowed_roots=VAULT_ROOTS)
        except ValueError as exc:
            errors.append(f"Unsafe managed file path: {exc}")
            continue
        if not managed.is_file():
            errors.append(f"Missing managed file: {managed}")
        elif strict:
            try:
                if managed.stat().st_size > 64 * 1024 * 1024:
                    errors.append(f"Managed file is too large to hash safely: {managed}")
                elif hashlib.sha256(managed.read_bytes()).hexdigest() != digest:
                    errors.append(f"Managed file hash mismatch: {managed}")
            except OSError as exc:
                errors.append(f"Cannot inspect managed file {managed}: {exc}")
    if index_entries != 1:
        errors.append("Managed-files manifest must contain exactly one routing-index entry")
    return errors


def _validate_wikilinks(vault: Path) -> list[str]:
    errors: list[str] = []
    for path in _safe_markdown_files(vault):
        try:
            if path.stat().st_size > 16 * 1024 * 1024:
                errors.append(f"Markdown file is too large to validate safely: {path}")
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            errors.append(f"Cannot inspect Markdown file {path}: {exc}")
            continue
        for target in re.findall(r"\[\[([^\]|#]+)", text):
            try:
                candidate = _safe_vault_join(vault, target + ("" if target.endswith(".md") else ".md"))
            except ValueError as exc:
                errors.append(f"Unsafe wikilink in {path}: {target} ({exc})")
                continue
            if not candidate.is_file():
                errors.append(f"Broken wikilink in {path}: {target}")
    return errors


def _safe_relative(value: str, *, allowed_roots: set[str] | None = None, allow_dot: bool = False) -> Path:
    normalized = value.replace("\\", "/")
    if allow_dot and normalized in {"", "."}:
        return Path()
    if not normalized or normalized.startswith(("/", "//")) or re.match(r"^[A-Za-z]:", normalized):
        raise ValueError(f"path must be relative: {value!r}")
    pure = PurePosixPath(normalized)
    if not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError(f"path traversal is not allowed: {value!r}")
    if allowed_roots is not None and pure.parts[0] not in allowed_roots:
        raise ValueError(f"path root is not managed: {value!r}")
    return Path(*pure.parts)


def _safe_vault_join(vault: Path, value: str, *, allowed_roots: set[str] | None = None) -> Path:
    relative = _safe_relative(value, allowed_roots=allowed_roots or VAULT_ROOTS)
    root = vault.resolve()
    current = root
    for part in relative.parts:
        current = current / part
        if _is_linklike(current):
            raise ValueError(f"path traverses a symlink or junction: {current}")
    if not current.resolve(strict=False).is_relative_to(root):
        raise ValueError(f"path escapes Vault: {value!r}")
    return current


def _safe_source_join(root_value: Path, value: str) -> Path:
    relative = _safe_relative(value, allow_dot=True)
    if _is_network_or_device_path(str(root_value)):
        raise ValueError(f"network or device source root is not allowed: {root_value!s}")
    root = root_value.resolve()
    parts = [part.lower() for part in root.parts]
    if any(parts[index:index + 2] == [".agents", "skills"] for index in range(max(0, len(parts) - 1))):
        raise ValueError(f".agents/skills source is forbidden: {root}")
    current = root
    for part in relative.parts:
        current = current / part
        if _is_linklike(current):
            raise ValueError(f"source traverses a symlink or junction: {current}")
    if not current.resolve(strict=False).is_relative_to(root):
        raise ValueError(f"source path escapes root: {value!r}")
    return current


def _is_network_or_device_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    return normalized.startswith("//")


def _safe_markdown_files(vault: Path) -> list[Path]:
    files: list[Path] = []
    root = vault.resolve()
    for directory_text, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
        directory = Path(directory_text)
        directory_names[:] = sorted(
            name for name in directory_names
            if not _is_linklike(directory / name)
            and (directory / name).resolve(strict=False).is_relative_to(root)
        )
        for name in sorted(file_names):
            path = directory / name
            if name.lower().endswith(".md") and not _is_linklike(path):
                files.append(path)
    return files


def _is_linklike(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
