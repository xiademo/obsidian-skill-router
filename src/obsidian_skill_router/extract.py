from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .io_utils import SKILL_METADATA_MAX_BYTES, read_text_bounded


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


def extract_skill_metadata(skill_dir: Path, detected_files: list[str]) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    metadata: dict[str, Any] = {}
    manifest_meta: dict[str, Any] = {}
    readme_meta: dict[str, Any] = {}
    skill_meta: dict[str, Any] = {}

    for filename in ["README.md", "skill.md", "SKILL.md"]:
        if filename in detected_files:
            try:
                extracted = _extract_markdown(skill_dir / filename)
                if filename == "README.md":
                    readme_meta.update(extracted)
                else:
                    skill_meta.update(extracted)
            except (OSError, UnicodeError, ValueError) as exc:
                warnings.append(f"Cannot read {filename}: {exc}")

    if "manifest.json" in detected_files:
        try:
            manifest = json.loads(
                read_text_bounded(
                    skill_dir / "manifest.json",
                    SKILL_METADATA_MAX_BYTES,
                    label="Skill manifest",
                )
            )
            if isinstance(manifest, dict):
                manifest_meta.update(_extract_manifest(manifest))
            else:
                warnings.append("manifest.json is not an object")
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            warnings.append(f"Cannot parse manifest.json: {exc}")

    metadata.update(manifest_meta)
    for key, value in readme_meta.items():
        metadata.setdefault(key, value)
    # SKILL.md/frontmatter is the Agent Skills source of truth. A manifest may
    # extend it, but must not silently override identity and activation text.
    metadata.update(skill_meta)
    return metadata, warnings


def _extract_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    aliases = {
        "name": ["name", "title", "skill_name"],
        "description": ["description", "summary"],
        "when_to_use": ["when_to_use", "use_when", "when", "usage"],
        "input_types": ["input_types", "inputs", "input"],
        "output_types": ["output_types", "outputs", "output"],
        "risk_level": ["risk_level", "risk"],
        "package_hint": ["package", "category", "domain"],
        "compatibility": ["compatibility"],
        "allowed_tools": ["allowed-tools", "allowed_tools"],
        "aliases": ["aliases"],
        "positive_triggers": ["positive_triggers", "positive-triggers"],
        "negative_triggers": ["negative_triggers", "negative-triggers", "when_not_to_use"],
        "actions": ["actions", "capabilities"],
        "artifacts": ["artifacts"],
        "route_mode": ["route_mode", "route-mode"],
        "trigger_mode": ["trigger_mode", "trigger-mode"],
        "side_effects": ["side_effects", "side-effects"],
        "permissions": ["permissions"],
        "requires": ["requires"],
        "conflicts": ["conflicts", "conflicts_with"],
        "workflow_next": ["workflow_next", "workflow-next"],
        "status": ["status"],
        "provider": ["provider"],
        "default_routable": ["default_routable", "default-routable"],
        "requires_explicit_user_intent": ["requires_explicit_user_intent", "requires-explicit-user-intent"],
        "network_access": ["network_access", "network-access"],
        "writes_files": ["writes_files", "writes-files"],
    }
    out: dict[str, Any] = {}
    for target, keys in aliases.items():
        for key in keys:
            if key in manifest and manifest[key] not in (None, ""):
                list_fields = {
                    "input_types", "output_types", "allowed_tools", "aliases",
                    "positive_triggers", "negative_triggers", "actions", "artifacts",
                    "side_effects", "permissions", "requires", "conflicts", "workflow_next",
                }
                boolean_fields = {"default_routable", "requires_explicit_user_intent", "network_access", "writes_files"}
                if target in list_fields:
                    out[target] = _string_list(manifest[key])
                elif target in boolean_fields:
                    out[target] = manifest[key] if isinstance(manifest[key], bool) else str(manifest[key]).strip().lower() in {"1", "true", "yes", "on"}
                else:
                    out[target] = str(manifest[key]).strip()
                break
    tags = manifest.get("tags")
    if tags and "tags" not in out:
        out["tags"] = _string_list(tags)
    return out


def _extract_markdown(path: Path) -> dict[str, Any]:
    text = read_text_bounded(path, SKILL_METADATA_MAX_BYTES, label="Skill Markdown")
    frontmatter, body = _frontmatter(text)
    sections = _sections(body)
    title = _title(body) or path.parent.name
    out: dict[str, Any] = {}
    if frontmatter:
        out.update(_extract_manifest(frontmatter))
    out.setdefault("name", title)
    first_para = _first_paragraph(body)
    if first_para:
        out.setdefault("description", first_para)

    section_aliases = {
        "description": ["description", "summary", "overview"],
        "when_to_use": [
            "when to use",
            "when_to_use",
            "use when",
            "use cases",
            "usage",
            "when",
            "triggers",
            "trigger",
            "when to call",
            "when to invoke",
        ],
        "input_types": ["inputs", "input", "input types", "input_types"],
        "output_types": ["outputs", "output", "output types", "output_types"],
        "risk_level": ["risk", "risk level", "risk_level", "safety"],
        "aliases": ["aliases", "alias"],
        "positive_triggers": ["positive triggers", "positive_triggers"],
        "negative_triggers": ["negative triggers", "negative_triggers", "when not to use"],
        "actions": ["actions", "capabilities"],
        "artifacts": ["artifacts"],
        "side_effects": ["side effects", "side_effects"],
        "permissions": ["permissions"],
        "requires": ["requires", "dependencies"],
        "conflicts": ["conflicts", "conflicts with", "conflicts_with"],
        "workflow_next": ["workflow next", "workflow_next", "next skills"],
    }
    list_sections = {
        "input_types", "output_types", "aliases", "positive_triggers", "negative_triggers",
        "actions", "artifacts", "side_effects", "permissions", "requires", "conflicts",
        "workflow_next",
    }
    for target, names in section_aliases.items():
        for name in names:
            if name in sections:
                value = _clean_section(sections[name])
                if target in list_sections:
                    section_values = _section_list(sections[name])
                    existing = _string_list(out.get(target, []))
                    out[target] = list(dict.fromkeys([*existing, *section_values]))
                elif target == "when_to_use" and value:
                    out.setdefault(target, _trim_sentence(value))
                elif value:
                    out.setdefault(target, value)
                break
    text_for_inference = " ".join(str(value) for value in [out.get("description", ""), body])
    out.setdefault("when_to_use", _infer_when_to_use(str(out.get("description", ""))))
    out.setdefault("when_to_use", _infer_when_to_use(body))
    if not out.get("when_to_use"):
        inferred = _when_from_description(str(out.get("description", "")), title)
        if inferred:
            out["when_to_use"] = inferred
    out.setdefault("input_types", _infer_types(text_for_inference, _INPUT_HINTS))
    out.setdefault("output_types", _infer_types(text_for_inference, _OUTPUT_HINTS))
    return out


_INPUT_HINTS = [
    "markdown", "pdf", "docx", "spreadsheet", "slides", "image", "screenshot",
    "audio", "video", "url", "api", "repo", "code", "diff", "design", "figma",
    "notion", "obsidian", "note", "database", "logs", "spec", "prompt",
]

_OUTPUT_HINTS = [
    "markdown", "note", "report", "summary", "plan", "tasks", "code", "tests",
    "dashboard", "chart", "skill", "vault", "json", "csv", "html", "doc",
    "slides", "design", "transcript", "review",
]


def _frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.DOTALL)
    if not match:
        return {}, text
    return _parse_frontmatter(match.group(1)), text[match.end():]


def _parse_frontmatter(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    current_key = ""
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        raw = lines[index]
        index += 1
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if not raw.startswith(" ") and ":" in raw:
            key, value = raw.split(":", 1)
            current_key = key.strip()
            value = value.strip()
            if value == "":
                result[current_key] = {}
            elif value in {">", ">-", ">+", "|", "|-", "|+"}:
                block: list[str] = []
                while index < len(lines) and (not lines[index].strip() or lines[index].startswith((" ", "\t"))):
                    block.append(lines[index].strip())
                    index += 1
                result[current_key] = ("\n" if value.startswith("|") else " ").join(part for part in block if part)
            elif value.startswith("[") and value.endswith("]"):
                result[current_key] = [part.strip().strip("'\"") for part in value[1:-1].split(",") if part.strip()]
            elif value.lower() in {"true", "false"}:
                result[current_key] = value.lower() == "true"
            else:
                result[current_key] = value.strip("'\"")
        elif current_key and raw.lstrip().startswith("- "):
            if not isinstance(result.get(current_key), list):
                result[current_key] = []
            result[current_key].append(raw.lstrip()[2:].strip().strip("'\""))
        elif current_key and isinstance(result.get(current_key), dict) and ":" in raw:
            key, value = raw.strip().split(":", 1)
            result[current_key][key.strip()] = value.strip().strip("'\"")
    if isinstance(result.get("metadata"), dict):
        metadata = result["metadata"]
        if "short-description" in metadata and "description" not in result:
            result["description"] = metadata["short-description"]
    return result


def _infer_when_to_use(text: str) -> str:
    patterns = [
        r"(Use when[^\n]{8,500})",
        r"(Use for[^\n]{8,500})",
        r"(Use this(?: skill)? when[^\n]{8,500})",
        r"(Invoke when[^\n]{8,500})",
        r"(Call when[^\n]{8,500})",
        r"(When [^\n]{8,500}, use[^\n]{4,500})",
        r"(When the user (?:asks|needs|wants)[^\n]{8,500})",
        r"(If the user (?:asks|needs|wants)[^\n]{8,500})",
        r"(For tasks involving[^\n]{8,500})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = _trim_sentence(match.group(1))
            if value.rstrip(".").endswith(":") or len(value) < 12:
                continue
            return value
    return ""


def _when_from_description(description: str, title: str) -> str:
    cleaned = _trim_sentence(description)
    if not cleaned:
        return ""
    lowered = cleaned.lower()
    if lowered.startswith(("use ", "use when", "use for", "when ", "if ")):
        return cleaned
    if title and lowered == title.strip().lower():
        return ""
    return f"Use for {cleaned[0].lower()}{cleaned[1:]}"


def _trim_sentence(value: str, limit: int = 220) -> str:
    cleaned = " ".join(value.strip().split())
    cleaned = cleaned.strip(" -")
    if not cleaned:
        return ""
    if len(cleaned) > limit:
        cleaned = cleaned[:limit].rsplit(" ", 1)[0].rstrip(",;:")
    if cleaned and cleaned[-1] not in ".!?":
        cleaned += "."
    return cleaned


def _infer_types(text: str, hints: list[str]) -> list[str]:
    lowered = text.lower()
    found = [hint for hint in hints if re.search(rf"\b{re.escape(hint)}s?\b", lowered)]
    return sorted(set(found))


def _sections(text: str) -> dict[str, str]:
    matches = list(HEADING_RE.finditer(text))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        name = _norm(match.group(2))
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[name] = text[start:end].strip()
    return sections


def _title(text: str) -> str:
    match = HEADING_RE.search(text)
    return match.group(2).strip() if match else ""


def _first_paragraph(text: str) -> str:
    body = HEADING_RE.sub("", text, count=1).strip()
    for block in re.split(r"\n\s*\n", body):
        cleaned = _clean_section(block)
        if cleaned and not cleaned.startswith("|") and len(cleaned.split()) >= 3:
            return cleaned
    return ""


def _clean_section(value: str) -> str:
    lines = []
    for line in value.splitlines():
        stripped = line.strip().lstrip("-*").strip()
        if stripped:
            lines.append(stripped)
    return " ".join(lines).strip()


def _section_list(value: str) -> list[str]:
    lines = [line.strip().lstrip("-*").strip() for line in value.splitlines() if line.strip()]
    if len(lines) > 1:
        return [line for line in lines if line]
    return _split_list(lines[0] if lines else "")


def _split_list(value: str) -> list[str]:
    if not value:
        return []
    parts = re.split(r"[,;|]|\s+-\s+|\n", value)
    return [part.strip(" .") for part in parts if part.strip(" .")]


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return _split_list(str(value))


def _norm(value: str) -> str:
    return re.sub(r"[_\-\s]+", " ", value.strip().lower())
