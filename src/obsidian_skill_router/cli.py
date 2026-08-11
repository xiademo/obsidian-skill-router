from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from . import __version__
from .config import load_config
from .eval import (
    enforce_eval_gates,
    load_records_from_vault,
    run_agent_first_eval_on_records,
    run_eval,
    run_eval_on_records,
    write_agent_first_eval_report,
    write_eval_report,
)
from .generate import dry_run_summary, generate_vault
from .io_utils import HOOK_INPUT_MAX_CHARS
from .reports import quality_report, risk_report, scan_report
from .registry import load_enrichment, load_routing_overlays
from .routing import route_vault
from .scan import scan_roots
from .validate import safe_vault_path, validate_vault


def main(argv: list[str] | None = None) -> int:
    raw_args = list(argv) if argv is not None else sys.argv[1:]
    json_requested = "--json" in raw_args
    command = next((value for value in raw_args if value in {"scan", "validate", "report", "route", "hook", "eval"}), "unknown")
    try:
        parser = _parser(json_errors=json_requested)
        args = parser.parse_args(raw_args)
        result = _dispatch(args)
        if args.json:
            _print_json(True, args.command, result)
        elif result is not None:
            print(_human(args.command, result))
        return 0
    except Exception as exc:
        if json_requested:
            _print_json(False, command, error={"type": type(exc).__name__, "message": str(exc)})
        else:
            print(f"error: {_terminal_text(exc)}", file=sys.stderr)
        return 1


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def _parser(*, json_errors: bool = False) -> argparse.ArgumentParser:
    parser_class = _JsonArgumentParser if json_errors else argparse.ArgumentParser
    parser = parser_class(prog="obsidian-skill-router", description="Generate an Obsidian skill routing vault from local skills.")
    parser.add_argument("--json", action="store_true", help="emit stable JSON output")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=parser_class)

    scan = sub.add_parser("scan", help="scan local skill folders and generate a vault")
    scan.add_argument("--skills-root", "--skills-dir", action="append", dest="skills_roots", help="local skill root; may be repeated")
    scan.add_argument("--vault", help="target Obsidian vault directory")
    scan.add_argument("--config", help="optional JSON or simple YAML config")
    scan.add_argument("--dry-run", action="store_true", help="scan and classify without writing vault files")
    scan.add_argument("--routing-rules", action="append", default=[], help="JSON routing overlay; may be repeated")
    scan.add_argument("--enrichment", action="append", default=[], help="offline enrichment proposal JSON; may be repeated")
    scan.add_argument("--legacy-readme-skills", action="store_true", help="explicitly discover deprecated README-only skills")

    validate = sub.add_parser("validate", help="validate generated vault structure and graph files")
    validate.add_argument("--vault", required=True, help="target Obsidian vault directory")
    validate.add_argument("--strict", action="store_true", help="reject legacy schema and non-canonical data")
    validate.add_argument("--check-sources", action="store_true", help="recompute source hashes and detect drift")

    report = sub.add_parser("report", help="summarize generated report files")
    report.add_argument("--vault", required=True, help="target Obsidian vault directory")

    route = sub.add_parser("route", help="recommend a local skill from a generated routing vault")
    route.add_argument("--vault", required=True, help="generated Obsidian routing vault")
    route.add_argument("--task", required=True, help="task text to route")
    route.add_argument("--explain", action="store_true", help="include up to two alternative candidates")
    route.add_argument("--strategy", choices=["legacy", "hybrid", "hybrid-graph"], default="legacy")
    route.add_argument("--profile", default="", help="optional routing profile name")

    hook = sub.add_parser("hook", help="thin host-hook adapter; reads one JSON event from stdin and never executes skills")
    hook.add_argument("--vault", required=True, help="generated Obsidian routing vault")
    hook.add_argument("--strategy", choices=["legacy", "hybrid", "hybrid-graph"], default="hybrid")
    hook.add_argument("--profile", default="", help="fallback routing profile name")

    eval_parser = sub.add_parser("eval", help="run repeatable routing eval cases")
    eval_parser.add_argument("--skills-root", "--skills-dir", action="append", dest="skills_roots", help="local skill root; may be repeated")
    eval_parser.add_argument("--vault", help="evaluate against a generated Obsidian routing vault")
    eval_parser.add_argument("--cases", required=True, help="JSON eval cases file")
    eval_parser.add_argument("--config", help="optional JSON or simple YAML config")
    eval_parser.add_argument("--report", help="optional Markdown eval report output path")
    eval_parser.add_argument("--agent-first", action="store_true", help="evaluate progressive route decisions and payload cost")
    eval_parser.add_argument("--audit", help="optional ignored local manual-audit JSON file")
    eval_parser.add_argument("--strategy", action="append", choices=["legacy", "hybrid", "hybrid-graph"], help="strategy to compare; may be repeated")
    eval_parser.add_argument("--gate-config", help="optional JSON release-gate thresholds")
    return parser


def _dispatch(args: argparse.Namespace) -> dict[str, object] | None:
    if args.command == "scan":
        config = load_config(args.config)
        roots = [Path(value) for value in (args.skills_roots or config.get("skills_roots", []))]
        vault_value = args.vault or config.get("vault")
        if not roots:
            raise ValueError("At least one --skills-root or config skills_roots entry is required")
        if not vault_value and not args.dry_run:
            raise ValueError("--vault or config vault is required unless --dry-run is used")
        overlays, overlay_warnings = load_routing_overlays(Path(value) for value in args.routing_rules)
        enrichment, candidates, enrichment_warnings = load_enrichment(Path(value) for value in args.enrichment)
        records, warnings = scan_roots(
            roots,
            package_rules=config.get("package_rules"),
            risk_keywords=config.get("risk_keywords"),
            routing_overlays=overlays,
            enrichment=enrichment,
            legacy_readme_skills=args.legacy_readme_skills or config.get("legacy_readme_skills", False),
            max_depth=config.get("max_depth", 6),
        )
        warnings = sorted(set([*warnings, *overlay_warnings, *enrichment_warnings, *config.get("_warnings", [])]))
        if args.dry_run:
            return dry_run_summary(records)
        return generate_vault(
            Path(vault_value),
            records,
            [str(root) for root in roots],
            warnings,
            candidates,
            max_workflow_next_per_source=config.get("max_workflow_next_per_source", 3),
        )

    if args.command == "validate":
        ok, errors = validate_vault(Path(args.vault), strict=args.strict, check_sources=args.check_sources)
        if not ok:
            raise ValueError("; ".join(errors))
        return {"valid": True, "errors": []}

    if args.command == "report":
        vault = Path(args.vault).resolve()
        reports = {}
        for name in [
            "scan-report.md",
            "risk-report.md",
            "quality-report.md",
            "calibration-report.md",
            "duplicate-conflict-report.md",
            "graph-quality-report.md",
        ]:
            path = safe_vault_path(vault, f"05_Reports/{name}", allowed_roots={"05_Reports"})
            if not path.is_file():
                raise ValueError(f"Missing report: {path}")
            if path.stat().st_size > 16 * 1024 * 1024:
                raise ValueError(f"Report exceeds 16777216 bytes: {path}")
            reports[name] = path.read_text(encoding="utf-8")
        return {"reports": reports}

    if args.command == "route":
        return route_vault(Path(args.vault), args.task, explain=args.explain, strategy=args.strategy, profile=args.profile).to_dict()

    if args.command == "hook":
        raw = sys.stdin.read(HOOK_INPUT_MAX_CHARS + 1)
        if len(raw) > HOOK_INPUT_MAX_CHARS:
            raise ValueError(f"hook input exceeds {HOOK_INPUT_MAX_CHARS} characters")
        if not raw.strip():
            raise ValueError("hook expects one JSON object on stdin")
        event = json.loads(raw)
        if not isinstance(event, dict):
            raise ValueError("hook event must be a JSON object")
        task = str(event.get("task") or event.get("prompt") or event.get("input") or "").strip()
        if not task:
            raise ValueError("hook event is missing task/prompt/input")
        profile = str(event.get("profile") or args.profile)
        explain = bool(event.get("explain", False))
        decision = route_vault(Path(args.vault), task, explain=explain, strategy=args.strategy, profile=profile).to_dict()
        return {"hook_schema": "0.1", "action": "route_only", "executes_skill": False, "decision": decision}

    if args.command == "eval":
        config = load_config(args.config)
        roots = [Path(value) for value in (args.skills_roots or config.get("skills_roots", []))]
        if args.vault:
            records, warnings = load_records_from_vault(Path(args.vault))
        else:
            if not roots:
                raise ValueError("At least one --skills-root, --vault, or config skills_roots entry is required")
            records, warnings = scan_roots(
                roots,
                package_rules=config.get("package_rules"),
                risk_keywords=config.get("risk_keywords"),
                legacy_readme_skills=config.get("legacy_readme_skills", False),
                max_depth=config.get("max_depth", 6),
            )
            warnings = sorted(set([*warnings, *config.get("_warnings", [])]))
        strategies = args.strategy or ["legacy"]
        results = {}
        for strategy in strategies:
            if args.agent_first:
                current = run_agent_first_eval_on_records(records, warnings, Path(args.cases), Path(args.audit) if args.audit else None, strategy=strategy)
            else:
                current = run_eval_on_records(records, warnings, Path(args.cases), strategy=strategy)
            results[strategy] = current
        result = results[strategies[0]] if len(strategies) == 1 else {"strategies": results}
        if args.report:
            if len(strategies) != 1:
                raise ValueError("--report requires exactly one --strategy")
            (write_agent_first_eval_report if args.agent_first else write_eval_report)(result, Path(args.report))
        if args.gate_config:
            _enforce_gates(result, Path(args.gate_config))
        return result
    return None


def _human(command: str, result: dict[str, object]) -> str:
    if command == "scan":
        return "scan complete: " + ", ".join(f"{key}={value}" for key, value in result.items())
    if command == "validate":
        return "vault valid"
    if command == "report":
        return "\n\n".join(_terminal_multiline(value) for value in result.get("reports", {}).values())
    if command == "route":
        primary = result.get("primary") or {}
        lines = [
            f"Route status: {_terminal_text(result.get('status', ''))}",
            f"Reason: {_terminal_text(result.get('reason', ''))}",
        ]
        if primary:
            lines.append(
                f"Primary skill: {_terminal_text(primary.get('name', ''))} ({_terminal_text(primary.get('id', ''))})"
            )
            lines.append(f"Skill card: {_terminal_text(primary.get('card_path', ''))}")
        if result.get("related_packages"):
            lines.append("Related packages: " + ", ".join(_terminal_text(value) for value in result["related_packages"]))
        budget = result.get("budget") or {}
        lines.append(f"Estimated response tokens: {budget.get('response_tokens_estimate', 0)}")
        return "\n".join(lines)
    if command == "hook":
        decision = result.get("decision", {})
        return json.dumps({"hook_schema": result.get("hook_schema"), "action": result.get("action"), "decision": decision}, ensure_ascii=False)
    if command == "eval":
        if isinstance(result.get("strategies"), dict):
            lines = ["Strategy comparison:"]
            for strategy, current in result["strategies"].items():
                metrics = current.get("metrics", {}) if isinstance(current, dict) else {}
                lines.append(
                    f"- {strategy}: Skill@1={_metric(metrics.get('skill_hit_at_1'))}, "
                    f"Skill@3={_metric(metrics.get('skill_hit_at_3'))}, "
                    f"MRR={_metric(metrics.get('mrr'))}, nDCG@5={_metric(metrics.get('ndcg_at_5'))}, "
                    f"FalseActivation={_metric(metrics.get('false_trigger_rate'))}"
                )
            return "\n".join(lines)
        metrics = result.get("metrics", {})
        if "primary_skill_precision_at_1" in metrics:
            return "\n".join(
                [
                    f"Primary Skill Precision@1: {_metric(metrics.get('primary_skill_precision_at_1'))}",
                    f"Candidate Recall@3: {_metric(metrics.get('candidate_recall_at_3'))}",
                    f"Correct Continue Rate: {_metric(metrics.get('correct_continue_rate'))}",
                    f"False Activation Rate: {_metric(metrics.get('false_activation_rate'))}",
                    f"Average Response Token Estimate: {metrics.get('average_response_tokens_estimate', 0):.2f}",
                    f"P95 Response Token Estimate: {metrics.get('p95_response_tokens_estimate', 0)}",
                ]
            )
        return "\n".join(
            [
                f"Package Hit@1: {_metric(metrics.get('package_hit_at_1'))}",
                f"Package Hit@2: {_metric(metrics.get('package_hit_at_2'))}",
                f"Skill Hit@1: {_metric(metrics.get('skill_hit_at_1'))}",
                f"Skill Hit@3: {_metric(metrics.get('skill_hit_at_3'))}",
                f"False Trigger Rate: {_metric(metrics.get('false_trigger_rate'))}",
                f"Ambiguity Correctly Flagged: {_metric(metrics.get('ambiguity_correctly_flagged'))}",
                f"Overconfident Top1 Count: {metrics.get('overconfident_top1_count', 0)}",
                f"Cross-Package Related Hit: {_metric(metrics.get('cross_package_related_hit'))}",
                f"Constraint Respect Rate: {_metric(metrics.get('constraint_respect_rate'))}",
            ]
        )
    return str(result)


def _terminal_text(value: object) -> str:
    return _terminal_clean(value, preserve_newlines=False)


def _terminal_multiline(value: object) -> str:
    return _terminal_clean(value, preserve_newlines=True)


def _terminal_clean(value: object, *, preserve_newlines: bool) -> str:
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))", "", text)
    bidi_controls = {"\u200e", "\u200f", "\u202a", "\u202b", "\u202c", "\u202d", "\u202e", "\u2066", "\u2067", "\u2068", "\u2069"}
    return "".join(
        character
        for character in text
        if character not in bidi_controls
        and (
            ord(character) >= 32 and not 127 <= ord(character) <= 159
            or preserve_newlines and character in {"\n", "\t"}
        )
    )


def _print_json(ok: bool, command: str, data: dict[str, object] | None = None, error: dict[str, str] | None = None) -> None:
    payload = {"ok": ok, "command": command}
    if ok:
        payload["data"] = data or {}
    else:
        payload["error"] = error or {"type": "Error", "message": "unknown error"}
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _metric(value: object) -> str:
    return "NA" if value is None else f"{float(value):.4f}"


def _enforce_gates(result: dict[str, object], path: Path) -> None:
    enforce_eval_gates(result, path)
