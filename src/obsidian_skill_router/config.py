from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .io_utils import GOVERNANCE_INPUT_MAX_BYTES, read_text_bounded


DEFAULT_PACKAGE_RULES: dict[str, list[str]] = {
    "document-processing": [
        "document", "pdf", "docx", "spreadsheet", "slides", "markdown", "ocr",
        "extract", "convert", "parse", "transcribe", "audio", "speech",
        "screenshot", "image", "media", "presentation",
    ],
    "career-resume": [
        "resume", "cv", "cover letter", "career", "job", "interview", "linkedin",
        "candidate", "hiring", "recruiter",
    ],
    "coding-workflow": [
        "code", "repo", "git", "test", "debug", "build", "review", "lint",
        "pull request", "api", "cli", "frontend", "ui", "ux", "react",
        "tailwind", "html", "css", "component", "implementation", "figma",
        "design system", "code connect",
    ],
    "research-writing": [
        "research", "paper", "citation", "literature", "draft", "write",
        "summarize", "academic", "report", "documentation", "meeting",
        "notion spec", "spec to implementation", "prd",
    ],
    "automation-ops": [
        "automation", "deploy", "monitor", "cron", "workflow", "ops", "ci",
        "pipeline", "schedule", "cloudflare", "workers", "pages", "queue",
        "cache reserve", "containers", "email routing", "network interconnect",
        "observability", "analytics engine", "stream", "zaraz", "workerd",
        "gateway", "wrangler", "durable objects", "r2", "d1", "tunnel",
        "terraform", "pulumi", "sandbox",
    ],
    "memory-knowledge": [
        "memory", "knowledge", "obsidian", "notes", "vault", "capture",
        "index", "wiki",
    ],
    "safety-governance": [
        "security", "safety", "risk", "governance", "policy", "approval",
        "secret", "threat", "audit", "ddos", "waf", "turnstile", "api shield",
        "bot management", "protection", "secrets store",
    ],
}

DEFAULT_RISK_KEYWORDS: dict[str, list[str]] = {
    "high": [
        "delete", "remove-item", "rm -rf", "credential", "secret", "token",
        "api key", "deploy", "production", "billing", "payment", "execute",
        "run command", "subprocess", "network sync", "publish", "install",
        "npm install", "pip install", "codex mcp add", "oauth", ".env",
        "ssh", "private key", "calendar", "email access",
    ],
    "medium": [
        "write", "modify", "filesystem", "file system", "http", "request",
        "browser", "login", "upload", "download", "database", "mcp",
        "external api", "api call", "network", "browser automation",
        "playwright", "notion", "slack", "github", "figma", "canva",
        "terraform", "pulumi", "env var", "local rest",
    ],
    "low": [
        "read", "summarize", "classify", "format", "local", "offline",
        "markdown", "inspect", "style", "template", "guide", "prompt",
    ],
}


def load_config(config_path: str | None) -> dict[str, Any]:
    if not config_path:
        return {}
    path = Path(config_path)
    text = read_text_bounded(path, GOVERNANCE_INPUT_MAX_BYTES, label="Configuration")
    if path.suffix.lower() == ".json":
        return _normalize_config(json.loads(text), path=path, json_config=True)
    if path.suffix.lower() in {".yaml", ".yml"}:
        return _normalize_config(_parse_simple_yaml(text), path=path, json_config=False)
    raise ValueError(f"Unsupported config type: {path.suffix}")


def _normalize_config(payload: Any, *, path: Path, json_config: bool) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(f"Configuration must be an object: {path}")
    allowed = {
        "schema_version", "skills_roots", "vault", "package_rules", "risk_keywords",
        "discovery", "routing", "graph", "policy",
    }
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"Unknown configuration fields in {path}: {', '.join(unknown)}")
    version = payload.get("schema_version")
    if version not in (None, "0.2"):
        raise ValueError(f"Unsupported configuration schema_version in {path}: {version!r}")

    result: dict[str, Any] = {}
    if "skills_roots" in payload:
        result["skills_roots"] = _string_list(payload["skills_roots"], "skills_roots", path)
    if "vault" in payload:
        if not isinstance(payload["vault"], str) or not payload["vault"].strip():
            raise ValueError(f"Configuration vault must be a non-empty string: {path}")
        result["vault"] = payload["vault"]

    warnings: list[str] = []
    if "package_rules" in payload:
        result["package_rules"] = _rule_mapping(payload["package_rules"], "package_rules", path)
        warnings.append("Legacy top-level package_rules configuration is deprecated; use routing.package_rules")
    if "risk_keywords" in payload:
        result["risk_keywords"] = _rule_mapping(payload["risk_keywords"], "risk_keywords", path)
        warnings.append("Legacy top-level risk_keywords configuration is deprecated; use policy.risk_keywords")

    nested = {
        name: _mapping(payload.get(name, {}), name, path)
        for name in ("discovery", "routing", "graph", "policy")
    }
    if not json_config and any(payload.get(section) for section in ("discovery", "routing", "graph", "policy")):
        raise ValueError(f"Nested configuration is supported only in JSON: {path}")
    nested_allowed = {
        "discovery": {"max_depth", "legacy_readme_skills"},
        "routing": {"package_rules"},
        "graph": {"max_workflow_next_per_source"},
        "policy": {"risk_keywords"},
    }
    for section, values in nested.items():
        extra = sorted(set(values) - nested_allowed[section])
        if extra:
            raise ValueError(f"Unknown {section} configuration fields in {path}: {', '.join(extra)}")

    discovery = nested["discovery"]
    if "max_depth" in discovery:
        value = discovery["max_depth"]
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 32:
            raise ValueError(f"discovery.max_depth must be an integer from 0 to 32: {path}")
        result["max_depth"] = value
    if "legacy_readme_skills" in discovery:
        if not isinstance(discovery["legacy_readme_skills"], bool):
            raise ValueError(f"discovery.legacy_readme_skills must be boolean: {path}")
        result["legacy_readme_skills"] = discovery["legacy_readme_skills"]
    if "package_rules" in nested["routing"]:
        result["package_rules"] = _rule_mapping(
            nested["routing"]["package_rules"], "routing.package_rules", path
        )
    if "risk_keywords" in nested["policy"]:
        result["risk_keywords"] = _rule_mapping(
            nested["policy"]["risk_keywords"], "policy.risk_keywords", path
        )
    if "max_workflow_next_per_source" in nested["graph"]:
        value = nested["graph"]["max_workflow_next_per_source"]
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 20:
            raise ValueError(f"graph.max_workflow_next_per_source must be an integer from 0 to 20: {path}")
        result["max_workflow_next_per_source"] = value

    if not json_config:
        warnings.append("Simple YAML configuration is legacy; use schema 0.2 JSON for nested settings")
    if warnings:
        result["_warnings"] = warnings
    return result


def _mapping(value: Any, field: str, path: Path) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"Configuration {field} must be an object: {path}")
    return value


def _string_list(value: Any, field: str, path: Path) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"Configuration {field} must be a non-empty list of strings: {path}")
    return [item.strip() for item in value]


def _rule_mapping(value: Any, field: str, path: Path) -> dict[str, list[str]]:
    mapping = _mapping(value, field, path)
    result: dict[str, list[str]] = {}
    for key, raw in mapping.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"Configuration {field} keys must be non-empty strings: {path}")
        values = [raw] if isinstance(raw, str) else raw
        if not isinstance(values, list) or not values or not all(isinstance(item, str) and item.strip() for item in values):
            raise ValueError(f"Configuration {field}.{key} must be a string or non-empty string list: {path}")
        result[key.strip()] = [item.strip() for item in values]
    return result


def merge_rules(custom: dict[str, Any] | None, defaults: dict[str, list[str]]) -> dict[str, list[str]]:
    merged = {key: list(value) for key, value in defaults.items()}
    if custom:
        for key, value in custom.items():
            if isinstance(value, str):
                merged[key] = [value]
            elif isinstance(value, list):
                merged[key] = [str(item) for item in value]
    return merged


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    current_key: str | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line.startswith(" ") and ":" in line:
            key, value = line.split(":", 1)
            current_key = key.strip()
            value = value.strip()
            if value == "":
                result[current_key] = {}
            elif value.startswith("[") and value.endswith("]"):
                result[current_key] = [item.strip().strip("'\"") for item in value[1:-1].split(",") if item.strip()]
            else:
                result[current_key] = value.strip("'\"")
        elif current_key and line.lstrip().startswith("- "):
            if not isinstance(result.get(current_key), list):
                result[current_key] = []
            result[current_key].append(line.lstrip()[2:].strip().strip("'\""))
        elif current_key and ":" in line:
            parent = result.setdefault(current_key, {})
            if not isinstance(parent, dict):
                continue
            key, value = line.strip().split(":", 1)
            parent[key.strip()] = [item.strip().strip("'\"") for item in value.strip().strip("[]").split(",") if item.strip()]
    return result
