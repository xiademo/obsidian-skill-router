from __future__ import annotations

import json
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from pathlib import Path

from obsidian_skill_router.cli import main
from obsidian_skill_router.eval import run_agent_first_eval_on_records, run_eval, write_agent_first_eval_report, write_eval_report
from obsidian_skill_router.extract import extract_skill_metadata
from obsidian_skill_router.graph import MVP_RELATIONS, build_package_edges, build_skill_edges, build_skill_edges_with_pruning
from obsidian_skill_router.generate import generate_vault
from obsidian_skill_router.models import GraphEdge, SkillRecord
from obsidian_skill_router.reports import high_risk_audit, risk_reasons
from obsidian_skill_router.routing import route_records, route_vault
from obsidian_skill_router.scan import scan_roots
from obsidian_skill_router.validate import validate_vault


class RouterTests(unittest.TestCase):
    def test_detects_manifest_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            skill = root / "code-review"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "# Code Review\n\n"
                "Review repository code and tests.\n\n"
                "## When to use\n\nUse when reviewing pull requests.\n\n"
                "## Inputs\n\nrepo, diff\n\n"
                "## Outputs\n\nfindings, summary\n",
                encoding="utf-8",
            )
            records, warnings = scan_roots([root])
            self.assertEqual(warnings, [])
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].package, "coding-workflow")
            self.assertIn("SKILL.md", records[0].detected_files)

    def test_manifest_overrides_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            skill = root / "resume"
            skill.mkdir(parents=True)
            (skill / "README.md").write_text("# Wrong\n\nGeneric text.", encoding="utf-8")
            (skill / "manifest.json").write_text(
                json.dumps(
                    {
                        "name": "Resume Builder",
                        "description": "Build a resume.",
                        "when_to_use": "Use for career resume work.",
                        "input_types": ["profile"],
                        "output_types": ["resume"],
                        "risk_level": "low",
                        "package": "career-resume",
                    }
                ),
                encoding="utf-8",
            )
            records, _ = scan_roots([root])
            self.assertEqual(records[0].name, "Resume Builder")
            self.assertEqual(records[0].package, "career-resume")
            self.assertEqual(records[0].risk_level, "low")

    def test_when_to_use_infers_from_description_and_trigger_variants(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "skills" / "router"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\n"
                "name: Router Helper\n"
                "description: Index local skills into a routing vault\n"
                "---\n\n"
                "# Router Helper\n\n"
                "## Triggers\n\n"
                "When the user asks for a package map or routing audit\n",
                encoding="utf-8",
            )
            metadata, warnings = extract_skill_metadata(skill, ["SKILL.md"])
            self.assertEqual(warnings, [])
            self.assertEqual(metadata["when_to_use"], "When the user asks for a package map or routing audit.")

    def test_when_to_use_falls_back_to_description(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "skills" / "summarizer"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "# Summarizer\n\nCreate concise reports from notes\n",
                encoding="utf-8",
            )
            metadata, _ = extract_skill_metadata(skill, ["SKILL.md"])
            self.assertEqual(metadata["when_to_use"], "Use for create concise reports from notes.")

    def test_high_risk_keywords(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            skill = root / "deploy"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nactions: [deploy]\n---\n\n# Deploy Production\n\nDeploy to production using tokens.\n\n"
                "## When to use\n\nUse for deployment.\n\n"
                "## Inputs\n\nservice, token\n\n"
                "## Outputs\n\ndeployment\n",
                encoding="utf-8",
            )
            records, _ = scan_roots([root])
            self.assertEqual(records[0].risk_level, "high")
            self.assertTrue(any("deploy" in reason for reason in risk_reasons(records[0])))

    def test_risk_inference_uses_word_boundaries_and_package_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            design = root / "design"
            design.mkdir(parents=True)
            (design / "SKILL.md").write_text(
                "# Design Guide\n\nReview execution quality in local interface writing.\n",
                encoding="utf-8",
            )
            ops = root / "ops"
            ops.mkdir(parents=True)
            (ops / "SKILL.md").write_text(
                "# Cloud Workflow\n\nCoordinate cloud automation workflow guidance.\n",
                encoding="utf-8",
            )
            records, _ = scan_roots([root])
            by_name = {record.name: record for record in records}
            self.assertEqual(by_name["Design Guide"].risk_level, "unknown")
            self.assertEqual(by_name["Cloud Workflow"].risk_level, "unknown")

    def test_high_risk_audit_buckets_reasons(self) -> None:
        record = SkillRecord(
            id="deploy",
            name="Deploy Prod",
            source_path="synthetic/deploy",
            detected_files=["SKILL.md"],
            description="Deploy to production with a token.",
            when_to_use="Use for production deploys.",
            input_types=["repo"],
            output_types=["deployment"],
            risk_level="high",
            package="automation-ops",
            confidence=0.9,
            actions=["deploy"],
            provenance={"risk_level": {"origin": "explicit", "evidence": "actions: deploy"}},
        )
        audit = high_risk_audit([record])
        self.assertEqual(len(audit["clearly_high_risk"]), 1)

    def test_scan_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            vault = Path(tmp) / "vault"
            skill = root / "notes"
            skill.mkdir(parents=True)
            (skill / "README.md").write_text(
                "# Note Capture\n\nCapture local Obsidian notes.\n\n"
                "## When to use\n\nUse for memory knowledge capture.\n\n"
                "## Inputs\n\nmarkdown\n\n"
                "## Outputs\n\nnote\n",
                encoding="utf-8",
            )
            code = main(["--json", "scan", "--skills-root", str(root), "--vault", str(vault), "--dry-run"])
            self.assertEqual(code, 0)
            self.assertFalse(vault.exists())

    def test_generate_and_validate_vault(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            vault = Path(tmp) / "vault"
            skill = root / "pdf"
            skill.mkdir(parents=True)
            (skill / "manifest.json").write_text(
                json.dumps(
                    {
                        "name": "PDF Extract",
                        "description": "Extract document text from PDF files.",
                        "when_to_use": "Use for document processing.",
                        "input_types": ["pdf"],
                        "output_types": ["markdown"],
                        "risk_level": "low",
                    }
                ),
                encoding="utf-8",
            )
            code = main(["scan", "--skills-root", str(root), "--vault", str(vault)])
            self.assertEqual(code, 0)
            ok, errors = validate_vault(vault)
            self.assertTrue(ok, errors)
            self.assertTrue((vault / "00_Router" / "Skill Package Map.md").is_file())
            self.assertTrue((vault / "00_Router" / "routing-index.json").is_file())
            self.assertTrue((vault / "03_Graph" / "skill_edges.csv").is_file())
            self.assertTrue((vault / "05_Reports" / "graph-quality-report.md").is_file())
            graph_quality = (vault / "05_Reports" / "graph-quality-report.md").read_text(encoding="utf-8")
            self.assertIn("## Relation Counts Before Pruning", graph_quality)
            self.assertIn("## Relation Counts After Pruning", graph_quality)
            self.assertIn("## Workflow Pruning Summary", graph_quality)
            self.assertIn("workflow_next before", graph_quality)
            self.assertIn("## Related Edge Noise", graph_quality)
            self.assertIn("## Suspicious Related Edges Without Strong Reason", graph_quality)
            self.assertIn("## Top High-Degree Skills", graph_quality)
            package_map = (vault / "00_Router" / "Skill Package Map.md").read_text(encoding="utf-8")
            self.assertIn("global routing entry point", package_map)
            card = next((vault / "01_Packages" / "document-processing" / "skills").glob("*.md")).read_text(encoding="utf-8")
            self.assertIn("## Fallbacks And Alternatives", card)
            self.assertIn("When not to use", card)

    def test_report_command_reads_generated_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            vault = Path(tmp) / "vault"
            skill = root / "memory"
            skill.mkdir(parents=True)
            (skill / "manifest.json").write_text(
                json.dumps(
                    {
                        "name": "Memory Capture",
                        "description": "Capture local Obsidian knowledge notes.",
                        "when_to_use": "Use for memory knowledge workflows.",
                        "input_types": ["markdown"],
                        "output_types": ["note"],
                        "risk_level": "low",
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(main(["scan", "--skills-root", str(root), "--vault", str(vault)]), 0)
            self.assertEqual(main(["report", "--vault", str(vault)]), 0)

    def test_generation_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            vault = Path(tmp) / "vault"
            for name in ["alpha", "beta"]:
                skill = root / name
                skill.mkdir(parents=True)
                (skill / "manifest.json").write_text(
                    json.dumps(
                        {
                            "name": name.title(),
                            "description": "Review code and tests.",
                            "when_to_use": "Use for coding workflow.",
                            "input_types": ["repo"],
                            "output_types": ["findings"],
                            "risk_level": "low",
                        }
                    ),
                    encoding="utf-8",
                )
            self.assertEqual(main(["scan", "--skills-root", str(root), "--vault", str(vault)]), 0)
            first = (vault / "00_Router" / "Global Skill Search Index.md").read_text(encoding="utf-8")
            self.assertEqual(main(["scan", "--skills-root", str(root), "--vault", str(vault)]), 0)
            second = (vault / "00_Router" / "Global Skill Search Index.md").read_text(encoding="utf-8")
            self.assertEqual(first, second)

    def test_source_skill_files_are_not_modified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            vault = Path(tmp) / "vault"
            skill = root / "research"
            skill.mkdir(parents=True)
            source = skill / "SKILL.md"
            content = (
                "# Research Writer\n\nWrite research summaries from notes.\n\n"
                "## When to use\n\nUse for research writing.\n\n"
                "## Inputs\n\nnotes\n\n"
                "## Outputs\n\nsummary\n"
            )
            source.write_text(content, encoding="utf-8")
            before = source.read_bytes()
            code = main(["scan", "--skills-root", str(root), "--vault", str(vault)])
            after = source.read_bytes()
            self.assertEqual(code, 0)
            self.assertEqual(before, after)

    def test_validate_fails_missing_vault(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ok, errors = validate_vault(Path(tmp) / "missing")
            self.assertFalse(ok)
            self.assertTrue(errors)

    def test_validate_fails_malformed_graph_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            for path in [
                vault / "00_Router",
                vault / "01_Packages",
                vault / "02_Workflows",
                vault / "03_Graph",
                vault / "04_Inbox" / "unclassified",
                vault / "04_Inbox" / "needs-review",
                vault / "05_Reports",
            ]:
                path.mkdir(parents=True)
            for package in [
                "document-processing",
                "career-resume",
                "coding-workflow",
                "research-writing",
                "automation-ops",
                "memory-knowledge",
                "safety-governance",
            ]:
                package_dir = vault / "01_Packages" / package
                (package_dir / "skills").mkdir(parents=True)
                (package_dir / "Package Index.md").write_text(
                    "| Skill | Best for | Inputs | Outputs | Risk | Confidence | Related / Next / Warnings |\n",
                    encoding="utf-8",
                )
            for name in ["Skill Package Map.md", "Global Skill Search Index.md", "Cross-Package Graph.md"]:
                (vault / "00_Router" / name).write_text("# x\n", encoding="utf-8")
            for name in ["scan-report.md", "risk-report.md", "quality-report.md", "graph-quality-report.md"]:
                (vault / "05_Reports" / name).write_text("# x\n", encoding="utf-8")
            (vault / "03_Graph" / "skill_edges.csv").write_text("bad,header\n", encoding="utf-8")
            (vault / "03_Graph" / "package_edges.csv").write_text(
                "source_package,target_package,relation,reason\n",
                encoding="utf-8",
            )
            ok, errors = validate_vault(vault)
            self.assertFalse(ok)
            self.assertTrue(any("Malformed CSV header" in error for error in errors))

    def test_eval_metrics_on_synthetic_fixtures(self) -> None:
        root = Path("tests") / "fixtures" / "eval_skills"
        cases = Path("tests") / "fixtures" / "eval_cases.json"
        result = run_eval([root], cases)
        self.assertEqual(result["metrics"]["package_hit_at_1"], 1.0)
        self.assertEqual(result["metrics"]["skill_hit_at_1"], 1.0)
        self.assertEqual(result["metrics"]["skill_hit_at_3"], 1.0)
        self.assertEqual(result["metrics"]["false_trigger_rate"], 0.0)

    def test_eval_command_emits_json_metrics(self) -> None:
        code = main(
            [
                "--json",
                "eval",
                "--skills-root",
                str(Path("tests") / "fixtures" / "eval_skills"),
                "--cases",
                str(Path("tests") / "fixtures" / "eval_cases.json"),
            ]
        )
        self.assertEqual(code, 0)

    def test_adversarial_eval_fixture_reports_failures(self) -> None:
        root = Path("tests") / "fixtures" / "eval_hard_skills"
        cases = Path("tests") / "fixtures" / "eval_hard_cases.json"
        result = run_eval([root], cases)

        self.assertGreaterEqual(result["cases"], 30)
        self.assertIn("ambiguous", result["cases_by_category"])
        self.assertIn("cross_package", result["cases_by_category"])
        self.assertIn("negative", result["cases_by_category"])
        self.assertIn("graph_noise", result["cases_by_category"])
        self.assertIn("risk_inference", result["cases_by_category"])
        self.assertIn("cross_package_hit", result["metrics"])
        self.assertIn("graph_over_expansion_count", result["metrics"])
        self.assertIn("risk_expectation_match_rate", result["metrics"])
        self.assertTrue(result["failures"] or result["borderline_cases"])

        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "adversarial-eval-report.md"
            write_eval_report(result, report_path)
            report = report_path.read_text(encoding="utf-8")
            self.assertIn("# Adversarial Eval Report", report)
            self.assertIn("## Failed Cases", report)
            self.assertIn("## Borderline Cases", report)
            self.assertIn("Cross-Package Hit", report)
            self.assertIn("## Recommended Next Rule Changes", report)

    def test_chinese_ambiguity_eval_reports_top2_and_constraints(self) -> None:
        root = Path("tests") / "fixtures" / "eval_hard_skills"
        cases = Path("tests") / "fixtures" / "eval_cn_ambiguity_cases.json"
        result = run_eval([root], cases)

        self.assertGreaterEqual(result["cases"], 20)
        self.assertIn("ambiguous", result["cases_by_category"])
        self.assertIn("cross_package", result["cases_by_category"])
        self.assertIn("graph_constraint", result["cases_by_category"])
        self.assertIn("similar_confusion", result["cases_by_category"])
        self.assertIn("package_hit_at_2", result["metrics"])
        self.assertIn("ambiguity_correctly_flagged", result["metrics"])
        self.assertIn("overconfident_top1_count", result["metrics"])
        self.assertIn("cross_package_related_hit", result["metrics"])
        self.assertIn("constraint_respect_rate", result["metrics"])
        self.assertGreater(result["metrics"]["package_hit_at_2"], 0.0)

        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "cn-ambiguity-eval-report.md"
            write_eval_report(result, report_path)
            report = report_path.read_text(encoding="utf-8")
            self.assertIn("Package Hit@2", report)
            self.assertIn("Ambiguity Correctly Flagged", report)
            self.assertIn("Overconfident Top1 Count", report)
            self.assertIn("Cross-Package Related Hit", report)
            self.assertIn("Constraint Respect Rate", report)
            self.assertIn("Top-2 Package Candidates", report)
            self.assertIn("Missing Related Reason", report)

    def test_route_uses_compact_index_and_requires_review_for_high_risk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            vault = Path(tmp) / "vault"
            for name, manifest in {
                "pdf-extract": {
                    "name": "PDF Extract",
                    "description": "Extract text from PDF files into markdown.",
                    "when_to_use": "Use when extracting text from a PDF.",
                    "input_types": ["pdf"],
                    "output_types": ["markdown"],
                    "risk_level": "low",
                    "package": "document-processing",
                },
                "prod-deploy": {
                    "name": "Production Deploy",
                    "description": "Deploy a service to production using credentials.",
                    "when_to_use": "Use for production deployment.",
                    "input_types": ["repo"],
                    "output_types": ["deployment"],
                    "risk_level": "high",
                    "package": "automation-ops",
                },
            }.items():
                skill = root / name
                skill.mkdir(parents=True)
                (skill / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(main(["scan", "--skills-root", str(root), "--vault", str(vault)]), 0)

            pdf = route_vault(vault, "Extract text from this PDF")
            self.assertEqual(pdf.status, "recommend")
            self.assertEqual(pdf.primary.name, "PDF Extract")
            card_path = pdf.to_dict()["primary"]["card_path"]
            self.assertTrue(card_path.endswith(f"{pdf.primary.id}.md"))
            self.assertTrue((vault / card_path).is_file())

            deploy = route_vault(vault, "Deploy this service to production with credentials")
            self.assertEqual(deploy.status, "review_required")
            self.assertEqual(deploy.primary.name, "Production Deploy")

            constrained = route_vault(vault, "Only extract PDF pages, do not deploy or suggest workflow")
            self.assertEqual(constrained.related_packages, [])
            self.assertTrue(constrained.constraint_detected)

            ambiguous = route_vault(vault, "帮我整理这个文件，最后给我一个能发给老师的版本")
            self.assertEqual(ambiguous.status, "continue_without_skill")
            self.assertIsNone(ambiguous.primary)

            unsupported = route_vault(vault, "Delete all old files without asking")
            self.assertEqual(unsupported.status, "continue_without_skill")
            self.assertIsNone(unsupported.primary)

    def test_route_does_not_select_explicit_only_skill_without_named_intent(self) -> None:
        records = [
            SkillRecord(
                id="code-review", name="Code Review", source_path="synthetic/code-review", detected_files=[],
                description="Review repository diffs for defects.", when_to_use="Use for repository code review.",
                input_types=["repo", "diff"], output_types=["findings"], risk_level="low", package="coding-workflow", confidence=0.9,
            ),
            SkillRecord(
                id="threat-model", name="Security Threat Model", source_path="synthetic/threat-model", detected_files=[],
                description="Write a repository threat model.", when_to_use="Trigger only when the user explicitly asks for threat modeling.",
                input_types=["repo"], output_types=["report"], risk_level="low", package="coding-workflow", confidence=0.9,
            ),
        ]
        from obsidian_skill_router.routing import route_records

        decision = route_records("Review this repository diff and report risky code changes", records)
        self.assertEqual(decision.primary.id, "code-review")

    def test_agent_first_eval_reports_decision_quality_and_cost(self) -> None:
        root = Path("tests") / "fixtures" / "eval_hard_skills"
        cases = Path("tests") / "fixtures" / "eval_agent_first_redteam_cases.json"
        records, warnings = scan_roots([root])
        result = run_agent_first_eval_on_records(records, warnings, cases)
        self.assertEqual(result["cases"], 30)
        self.assertEqual(result["counts"]["red_team_cases"], 30)
        for metric in [
            "primary_skill_precision_at_1",
            "candidate_recall_at_3",
            "correct_continue_rate",
            "false_activation_rate",
            "high_risk_review_required_rate",
            "average_response_tokens_estimate",
            "p95_response_tokens_estimate",
            "candidate_payload_waste_tokens",
        ]:
            self.assertIn(metric, result["metrics"])
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "agent-first-eval-report.md"
            write_agent_first_eval_report(result, report_path)
            report = report_path.read_text(encoding="utf-8")
            self.assertIn("# Agent-First Route Eval Report", report)
            self.assertIn("## Balanced Coverage", report)
            self.assertIn("## Real Workload", report)
            self.assertIn("Candidate Payload Waste Tokens", report)

    def test_graph_edges_use_mvp_relation_set(self) -> None:
        records = [
            SkillRecord(
                id="lint",
                name="Lint Code",
                source_path="synthetic/lint",
                detected_files=["SKILL.md"],
                description="Inspect repository code before tests.",
                when_to_use="Use before test execution.",
                input_types=["repo"],
                output_types=["code"],
                risk_level="low",
                package="coding-workflow",
                confidence=0.9,
            ),
            SkillRecord(
                id="test",
                name="Test Code",
                source_path="synthetic/test",
                detected_files=["SKILL.md"],
                description="Run tests as an alternative fallback.",
                when_to_use="Use after code is prepared.",
                input_types=["code"],
                output_types=["report"],
                risk_level="low",
                package="coding-workflow",
                confidence=0.9,
                alternate_packages=["automation-ops"],
            ),
            SkillRecord(
                id="test-copy",
                name="Test Code",
                source_path="synthetic/test-copy",
                detected_files=["SKILL.md"],
                description="Run tests for reports.",
                when_to_use="Use after code is prepared.",
                input_types=["code"],
                output_types=["report"],
                risk_level="low",
                package="coding-workflow",
                confidence=0.9,
            ),
            SkillRecord(
                id="test-runner",
                name="Test Runner",
                source_path="synthetic/test-runner",
                detected_files=["SKILL.md"],
                description="Execute validation tests for reports.",
                when_to_use="Use after code is prepared.",
                input_types=["code"],
                output_types=["report"],
                risk_level="low",
                package="coding-workflow",
                confidence=0.9,
            ),
            SkillRecord(
                id="deploy",
                name="Deploy Service",
                source_path="synthetic/deploy",
                detected_files=["SKILL.md"],
                description="Deploy a production service.",
                when_to_use="Use for production deployment.",
                input_types=["report"],
                output_types=["deployment"],
                risk_level="high",
                package="automation-ops",
                confidence=0.9,
            ),
        ]
        relations = {edge.relation for edge in [*build_skill_edges(records), *build_package_edges(records)]}
        self.assertTrue(relations <= MVP_RELATIONS)
        for relation in ["related", "duplicates", "conflicts_with", "governs"]:
            self.assertIn(relation, relations)
        self.assertNotIn("complements", relations)
        self.assertNotIn("fallback_to", relations)

    def test_workflow_pruning_keeps_strong_matches_and_top_n(self) -> None:
        records = [
            SkillRecord(
                id="source",
                name="Build Artifact",
                source_path="synthetic/source",
                detected_files=["SKILL.md"],
                description="Build deployment artifacts before release.",
                when_to_use="Use before the next deployment step.",
                input_types=["repo"],
                output_types=["deployment"],
                risk_level="low",
                package="automation-ops",
                confidence=0.9,
                workflow_next=[f"Deploy Target {index}" for index in range(5)],
            )
        ]
        for index in range(5):
            records.append(
                SkillRecord(
                    id=f"target-{index}",
                    name=f"Deploy Target {index}",
                    source_path=f"synthetic/target-{index}",
                    detected_files=["SKILL.md"],
                    description="Deploy a prepared artifact.",
                    when_to_use="Use after deployment artifacts are ready.",
                    input_types=["deployment"],
                    output_types=["report"],
                    risk_level="medium",
                    package="automation-ops",
                    confidence=0.9,
                )
            )
        edges, pruning = build_skill_edges_with_pruning(records, max_workflow_next_per_source=3)
        workflow = [edge for edge in edges if edge.relation == "workflow_next" and edge.source == "source"]
        self.assertEqual(len(workflow), 5)
        self.assertEqual(pruning.before_count, 0)
        self.assertTrue(all(edge.origin == "explicit-metadata" and edge.review_state == "confirmed" for edge in workflow))

    def test_workflow_pruning_downgrades_or_removes_weak_candidates(self) -> None:
        records = [
            SkillRecord(
                id="doc",
                name="Document Summary",
                source_path="synthetic/doc",
                detected_files=["SKILL.md"],
                description="Summarize a document.",
                when_to_use="Use for document processing.",
                input_types=["pdf"],
                output_types=["report"],
                risk_level="low",
                package="document-processing",
                confidence=0.9,
            ),
            SkillRecord(
                id="research",
                name="Research Draft",
                source_path="synthetic/research",
                detected_files=["SKILL.md"],
                description="Draft research from a report.",
                when_to_use="Use for research writing.",
                input_types=["report"],
                output_types=["summary"],
                risk_level="low",
                package="research-writing",
                confidence=0.9,
            ),
            SkillRecord(
                id="generic",
                name="Generic Report Formatter",
                source_path="synthetic/generic",
                detected_files=["SKILL.md"],
                description="Format a report.",
                when_to_use="Use for formatting.",
                input_types=["report"],
                output_types=["summary"],
                risk_level="low",
                package="document-processing",
                confidence=0.9,
            ),
        ]
        edges, pruning = build_skill_edges_with_pruning(records)
        self.assertFalse(any(edge.source == "doc" and edge.target == "research" and edge.relation == "workflow_next" for edge in edges))
        self.assertEqual(pruning.before_count, 0)
        self.assertEqual(pruning.removed_workflow_edges, [])

    def test_sequence_terms_require_word_boundaries_for_workflow_edges(self) -> None:
        records = [
            SkillRecord(
                id="nextjs-auth",
                name="Next.js Auth Guide",
                source_path="synthetic/nextjs-auth",
                detected_files=["SKILL.md"],
                description="Build Next.js authentication guides for app projects.",
                when_to_use="Use for Next.js authentication docs.",
                input_types=["repo"],
                output_types=["guide"],
                risk_level="low",
                package="coding-workflow",
                confidence=0.9,
            ),
            SkillRecord(
                id="auth-ui",
                name="Auth UI Guide",
                source_path="synthetic/auth-ui",
                detected_files=["SKILL.md"],
                description="Review authentication guide for app projects.",
                when_to_use="Use for app authentication docs.",
                input_types=["design"],
                output_types=["review"],
                risk_level="low",
                package="coding-workflow",
                confidence=0.9,
            ),
        ]
        edges, pruning = build_skill_edges_with_pruning(records)
        self.assertFalse(any(edge.relation == "workflow_next" for edge in edges))
        self.assertEqual(pruning.before_count, 0)

    def test_registry_discovery_skips_resource_readmes_but_keeps_nested_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            skill = root / "router"
            nested = skill / "nested"
            reference = skill / "references" / "example"
            nested.mkdir(parents=True)
            reference.mkdir(parents=True)
            (skill / "SKILL.md").write_text("# Router\n\nRoute local skills.\n", encoding="utf-8")
            (nested / "SKILL.md").write_text("# Nested Helper\n\nHelp route local skills.\n", encoding="utf-8")
            (reference / "README.md").write_text("# Fake Skill\n", encoding="utf-8")
            records, _ = scan_roots([root])
            self.assertEqual({record.name for record in records}, {"Router", "Nested Helper"})

    def test_stable_id_does_not_depend_on_absolute_workspace(self) -> None:
        ids = []
        with tempfile.TemporaryDirectory() as left, tempfile.TemporaryDirectory() as right:
            for base in (left, right):
                skill = Path(base) / "skills" / "same" / "nested"
                skill.mkdir(parents=True)
                (skill / "SKILL.md").write_text("# Stable Skill\n\nRoute stable content.\n", encoding="utf-8")
                records, _ = scan_roots([Path(base) / "skills"])
                ids.append(records[0].id)
        self.assertEqual(ids[0], ids[1])

    def test_hybrid_policy_gates_manual_and_returns_no_skill_for_ocr(self) -> None:
        records = [
            SkillRecord(
                id="headroom", name="Headroom", source_path="synthetic/headroom", detected_files=["SKILL.md"],
                description="Compress large agent context and tokens.", when_to_use="Use for context compression.",
                package="coding-workflow", confidence=0.9, aliases=["context compressor"], route_mode="manual",
            ),
            SkillRecord(
                id="router", name="Skill Routing Rules", source_path="synthetic/router", detected_files=["SKILL.md"],
                description="Audit and improve Codex skill routing hit rate.", when_to_use="Use for skill routing audits.",
                package="safety-governance", confidence=0.9, aliases=["skill-routing-rules"],
            ),
        ]
        implicit = route_records("compress a large context", records, strategy="hybrid").to_dict()
        self.assertIn("headroom", {item["id"] for item in implicit["excluded_candidates"]})
        explicit = route_records("use Headroom to compress this context", records, strategy="hybrid").to_dict()
        self.assertEqual(explicit["status"], "review_required")
        audit = route_records("audit this product's skill routing rules", records, strategy="hybrid").to_dict()
        self.assertEqual(audit["primary"]["id"], "router")
        ocr = route_records("OCR a scanned PDF", records, strategy="hybrid").to_dict()
        self.assertEqual(ocr["decision"]["type"], "continue_without_skill")

    def test_managed_generation_preserves_user_files_and_removes_stale_cards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            vault = Path(tmp) / "vault"
            for name in ("alpha", "beta"):
                skill = root / name
                skill.mkdir(parents=True)
                (skill / "SKILL.md").write_text(f"# {name.title()} Skill\n\nReview code repositories.\n", encoding="utf-8")
            records, warnings = scan_roots([root])
            generate_vault(vault, records, [str(root)], warnings)
            stale = vault / records[1].package / "unused" if False else vault / "01_Packages" / records[1].package / "skills" / f"{records[1].id}.md"
            user_file = vault / "my-notes.md"
            user_file.write_text("user owned", encoding="utf-8")
            (root / "beta" / "SKILL.md").unlink()
            updated, warnings = scan_roots([root])
            generate_vault(vault, updated, [str(root)], warnings)
            self.assertTrue(user_file.is_file())
            self.assertFalse(stale.exists())

    def test_strict_source_validation_detects_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            vault = Path(tmp) / "vault"
            skill = root / "router"
            skill.mkdir(parents=True)
            source = skill / "SKILL.md"
            source.write_text("# Router\n\nRoute local skills.\n", encoding="utf-8")
            records, warnings = scan_roots([root])
            generate_vault(vault, records, [str(root)], warnings)
            ok, errors = validate_vault(vault, strict=True, check_sources=True)
            self.assertTrue(ok, errors)
            source.write_text("# Router\n\nChanged content.\n", encoding="utf-8")
            ok, errors = validate_vault(vault, strict=True, check_sources=True)
            self.assertFalse(ok)
            self.assertTrue(any("Source hash drift" in error for error in errors))

    def test_json_stdout_is_one_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            skill = root / "router"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("# Router\n\nRoute local skills.\n", encoding="utf-8")
            stream = io.StringIO()
            with redirect_stdout(stream):
                code = main(["--json", "scan", "--skills-root", str(root), "--dry-run"])
            self.assertEqual(code, 0)
            lines = [line for line in stream.getvalue().splitlines() if line.strip()]
            self.assertEqual(len(lines), 1)
            self.assertTrue(json.loads(lines[0])["ok"])

    def test_multiline_frontmatter_and_short_tokens_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "skills" / "router"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: KV Router\ndescription: >\n  Route .md and Next.js artifacts\n  with AI and KV inputs.\naliases:\n  - JD Router\n---\n\n# KV Router\n",
                encoding="utf-8",
            )
            metadata, warnings = extract_skill_metadata(skill, ["SKILL.md"])
            self.assertEqual(warnings, [])
            self.assertIn("Next.js", metadata["description"])
            self.assertEqual(metadata["aliases"], ["JD Router"])

    def test_multi_skill_plan_uses_confirmed_order_and_escalates_risk(self) -> None:
        records = [
            SkillRecord(
                id="research", name="Research Sources", source_path="synthetic/research", detected_files=["SKILL.md"],
                description="Search literature and gather citations.", when_to_use="Use for research sources.",
                output_types=["sources"], package="research-writing", confidence=0.9,
            ),
            SkillRecord(
                id="report", name="Write Report", source_path="synthetic/report", detected_files=["SKILL.md"],
                description="Write a structured report from sources.", when_to_use="Use to write final report.",
                input_types=["sources"], output_types=["report"], package="research-writing", confidence=0.9,
                risk_level="high", actions=["publish"],
            ),
        ]
        edges = [GraphEdge("research", "report", "workflow_next", "explicit contract", 1.0, "explicit", "confirmed", "sources -> report")]
        decision = route_records("research sources then write report", records, strategy="hybrid", edges=edges).to_dict()
        self.assertEqual(decision["decision"]["type"], "review_required")
        self.assertEqual([step["skill"]["id"] for step in decision["plan"]], ["research", "report"])

    def test_hook_is_route_only_json_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            vault = Path(tmp) / "vault"
            skill = root / "router"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: Skill Routing Rules\naliases: [skill-routing-rules]\n---\n\n# Skill Routing Rules\n\nAudit and improve skill routing rules.\n",
                encoding="utf-8",
            )
            records, warnings = scan_roots([root])
            generate_vault(vault, records, [str(root)], warnings)
            output = io.StringIO()
            event = io.StringIO(json.dumps({"task": "use skill-routing-rules to audit routing"}))
            with patch("sys.stdin", event), redirect_stdout(output):
                code = main(["--json", "hook", "--vault", str(vault), "--strategy", "hybrid"])
            self.assertEqual(code, 0)
            envelope = json.loads(output.getvalue())
            self.assertFalse(envelope["data"]["executes_skill"])
            self.assertEqual(envelope["data"]["action"], "route_only")
            direct = route_vault(vault, "use skill-routing-rules to audit routing", strategy="hybrid").to_dict()
            self.assertEqual(envelope["data"]["decision"], direct)


if __name__ == "__main__":
    unittest.main()
