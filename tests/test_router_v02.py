from __future__ import annotations

import hashlib
import io
import json
import os
import statistics
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from obsidian_skill_router.cli import _enforce_gates, main
from obsidian_skill_router.eval import _ambiguous_reasons, run_agent_first_eval_on_records
from obsidian_skill_router.generate import generate_vault
from obsidian_skill_router.graph import build_skill_edges
from obsidian_skill_router.models import GraphEdge, SkillRecord
from obsidian_skill_router.registry import load_enrichment, load_routing_overlays
from obsidian_skill_router.routing import apply_graph_shadow, rank_records, route_records, route_vault
from obsidian_skill_router.scan import scan_roots
from obsidian_skill_router.validate import validate_vault


class RouterV02Tests(unittest.TestCase):
    def test_routing_overlay_has_governed_priority_and_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            skill = root / "router"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: Router\n---\n\n# Router\n\nRoute local capabilities.\n",
                encoding="utf-8",
            )
            rules = Path(tmp) / "routing-rules.json"
            rules.write_text(
                json.dumps(
                    {
                        "skills": [
                            {
                                "name": "Router",
                                "package": "safety-governance",
                                "aliases": ["governed-router"],
                                "route_mode": "manual",
                                "risk_level": "high",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            overlays, warnings = load_routing_overlays([rules])
            self.assertEqual(warnings, [])
            records, scan_warnings = scan_roots([root], routing_overlays=overlays)
            self.assertEqual(scan_warnings, [])
            record = records[0]
            self.assertEqual(record.package, "safety-governance")
            self.assertEqual(record.route_mode, "manual")
            self.assertEqual(record.risk_level, "high")
            self.assertIn("governed-router", record.aliases)
            self.assertEqual(record.provenance["route_mode"]["origin"], "routing-overlay")
            implicit = route_records("route local capabilities", records, strategy="hybrid").to_dict()
            self.assertEqual(implicit["decision"]["type"], "continue_without_skill")
            explicit = route_records("use governed-router", records, strategy="hybrid").to_dict()
            self.assertEqual(explicit["decision"]["type"], "review_required")

    def test_routing_overlay_rejects_path_like_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            skill = root / "router"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("# Router\n\nRoute local capabilities.\n", encoding="utf-8")
            rules = Path(tmp) / "rules.json"
            rules.write_text(
                json.dumps({"skills": [{"name": "Router", "package": "../../outside"}]}),
                encoding="utf-8",
            )
            overlays, _ = load_routing_overlays([rules])
            with self.assertRaisesRegex(ValueError, "Unsafe routing package name"):
                scan_roots([root], routing_overlays=overlays)

    def test_routing_overlay_schema_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            skill = root / "router"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("# Router\n\nRoute local skills.\n", encoding="utf-8")
            invalid = [
                {"route_mode": "sometimes"},
                {"trigger_mode": "maybe"},
                {"default_routable": "false"},
                {"aliases": {"unsafe": "mapping"}},
                {"topic_risk": "high"},
            ]
            for values in invalid:
                with self.subTest(values=values), self.assertRaises(ValueError):
                    scan_roots([root], routing_overlays={"router": {"name": "Router", **values}})

            records, warnings = scan_roots(
                [root],
                routing_overlays={
                    "router": {
                        "name": "Router",
                        "use_when": ["audit routing", "review activation"],
                        "topic_risk": "high-topic",
                    }
                },
            )
            self.assertEqual(warnings, [])
            self.assertEqual(records[0].positive_triggers, ["audit routing", "review activation"])
            self.assertEqual(records[0].topic_risk, "high-topic")

    def test_unknown_source_modes_are_normalized_to_manual(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            skill = root / "router"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: Router\nroute_mode: surprise\ntrigger_mode: sometimes\n---\n\n# Router\n\nRoute local skills.\n",
                encoding="utf-8",
            )
            records, warnings = scan_roots([root])
            record = records[0]
            self.assertEqual(record.route_mode, "manual")
            self.assertEqual(record.trigger_mode, "manual")
            self.assertFalse(record.default_routable)
            self.assertTrue(record.requires_explicit_user_intent)
            self.assertTrue(any("Unknown source route_mode" in warning for warning in warnings))
            self.assertTrue(any("Unknown source trigger_mode" in warning for warning in warnings))
            implicit = route_records("route local skills", records, strategy="hybrid").to_dict()
            self.assertEqual(implicit["decision"]["type"], "continue_without_skill")
            explicit = route_records("use Router", records, strategy="hybrid").to_dict()
            self.assertEqual(explicit["decision"]["type"], "review_required")

    def test_policy_gate_is_strategy_independent(self) -> None:
        policies = [
            {"route_mode": "manual"},
            {"trigger_mode": "explicit-only"},
            {"default_routable": False},
        ]
        for policy in policies:
            record = SkillRecord(
                id="controlled-writer", name="Controlled Writer", source_path="synthetic", detected_files=["SKILL.md"],
                description="Write a controlled local report.", when_to_use="Use to write a local report.",
                package="research-writing", confidence=0.9, **policy,
            )
            for strategy in ("legacy", "hybrid"):
                with self.subTest(policy=policy, strategy=strategy):
                    implicit = route_records("write a controlled local report", [record], strategy=strategy).to_dict()
                    self.assertEqual(implicit["decision"]["type"], "continue_without_skill")
                    self.assertEqual(implicit["excluded_candidates"][0]["reason_code"], "explicit intent required")
                    explicit = route_records("use Controlled Writer", [record], strategy=strategy).to_dict()
                    self.assertEqual(explicit["decision"]["type"], "review_required")

    def test_policy_filtered_documents_do_not_corrupt_bm25_idf(self) -> None:
        eligible = SkillRecord(
            "calibrator", "Lunar Calibrator", "synthetic/calibrator", ["SKILL.md"],
            description="Create a lunar calibration report.",
            when_to_use="Use for lunar calibration reports.",
            package="research-writing", confidence=0.9,
        )
        gated = [
            SkillRecord(
                f"manual-{index}", f"Manual {index}", f"synthetic/{index}", ["SKILL.md"],
                description="Create a lunar calibration report.",
                when_to_use="Use for lunar calibration reports.",
                package="research-writing", confidence=0.9, route_mode="manual",
            )
            for index in range(100)
        ]
        decision = route_records(
            "lunar calibration report", [eligible, *gated], strategy="hybrid"
        ).to_dict()
        self.assertEqual(decision["primary"]["id"], "calibrator")

    def test_short_cjk_queries_require_exact_alias_but_short_technical_tokens_survive(self) -> None:
        cjk_record = SkillRecord(
            "writer", "Writing Helper", "synthetic/writer", ["SKILL.md"],
            description="处理文档并生成报告", when_to_use="用于文档处理",
            package="document-processing", confidence=0.9,
        )
        self.assertIsNone(route_records("文", [cjk_record], strategy="hybrid").to_dict()["primary"])
        exact_cjk = SkillRecord(
            "wen", "文", "synthetic/wen", ["SKILL.md"],
            description="处理文档", package="document-processing", confidence=0.9,
        )
        self.assertEqual(route_records("文", [exact_cjk], strategy="hybrid").to_dict()["primary"]["id"], "wen")

        technical = [
            SkillRecord("md", "Markdown", "md", ["SKILL.md"], description="Convert .md files.", package="document-processing", confidence=0.9),
            SkillRecord("next", "Next Builder", "next", ["SKILL.md"], description="Build Next.js applications.", package="coding-workflow", confidence=0.9),
            SkillRecord("ai", "AI Analyst", "ai", ["SKILL.md"], description="Analyze AI systems.", package="research-writing", confidence=0.9),
            SkillRecord("kv", "KV Inspector", "kv", ["SKILL.md"], description="Inspect KV storage.", package="coding-workflow", confidence=0.9),
            SkillRecord("jd", "JD Analyzer", "jd", ["SKILL.md"], description="Analyze JD requirements.", package="career-resume", confidence=0.9),
        ]
        for query, expected in ((".md", "md"), ("Next.js", "next"), ("AI", "ai"), ("KV", "kv"), ("JD", "jd")):
            with self.subTest(query=query):
                self.assertEqual(route_records(query, technical, strategy="hybrid").to_dict()["primary"]["id"], expected)

    def test_declared_low_risk_cannot_hide_destructive_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            skill = root / "cleaner"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: Cleaner\nrisk_level: low\nactions: [delete files]\n---\n\n# Cleaner\n\nDelete selected local files.\n",
                encoding="utf-8",
            )
            records, _ = scan_roots([root])
            record = records[0]
            self.assertEqual(record.action_risk, "high")
            self.assertEqual(record.risk_level, "high")
            decision = route_records("use Cleaner", records, strategy="hybrid").to_dict()
            self.assertEqual(decision["decision"]["type"], "review_required")

    def test_route_loader_rejects_tampered_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            vault = Path(tmp) / "vault"
            skill = root / "router"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("# Router\n\nRoute local skills.\n", encoding="utf-8")
            records, warnings = scan_roots([root])
            generate_vault(vault, records, [str(root)], warnings)
            index = vault / "00_Router" / "routing-index.json"
            original = json.loads(index.read_text(encoding="utf-8"))
            manifest_path = vault / ".obsidian-skill-router" / "managed-files.json"

            def write_registry(payload: dict[str, object], *, sync_manifest: bool = True) -> None:
                index.write_text(json.dumps(payload), encoding="utf-8")
                if not sync_manifest:
                    return
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                entry = next(item for item in manifest["files"] if item["path"] == "00_Router/routing-index.json")
                entry["sha256"] = hashlib.sha256(index.read_bytes()).hexdigest()
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            harmless_tamper = json.loads(json.dumps(original))
            harmless_tamper["skills"][0]["description"] = "tampered but structurally valid"
            write_registry(harmless_tamper, sync_manifest=False)
            with self.assertRaisesRegex(ValueError, "hash does not match"):
                route_vault(vault, "use Router", strategy="hybrid")
            write_registry(original)
            mutations = {
                "id traversal": ("id", "../../outside"),
                "package traversal": ("package", "../outside"),
                "card traversal": ("card_path", "../../outside.md"),
                "source traversal": ("source_relative_path", "../../outside"),
                "non-list aliases": ("aliases", {"bad": "shape"}),
                "string boolean": ("default_routable", "false"),
                "unknown mode": ("route_mode", "sometimes"),
            }
            for label, (field, value) in mutations.items():
                with self.subTest(label=label):
                    payload = json.loads(json.dumps(original))
                    payload["skills"][0][field] = value
                    write_registry(payload)
                    with self.assertRaises(ValueError):
                        route_vault(vault, "use Router", strategy="hybrid")
            payload = json.loads(json.dumps(original))
            skill_id = payload["skills"][0]["id"]
            payload["edges"].append(
                {
                    "source": skill_id, "target": skill_id, "relation": "related", "reason": "tampered",
                    "confidence": 1.0, "origin": "mutual-knn", "review_state": "confirmed",
                    "evidence": "none", "rule_version": "0.2",
                }
            )
            write_registry(payload)
            with self.assertRaisesRegex(ValueError, "Candidate routing edge cannot be confirmed"):
                route_vault(vault, "use Router", strategy="hybrid")

    def test_approved_enrichment_cannot_confirm_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            source = root / "source"
            target = root / "target"
            source.mkdir(parents=True)
            target.mkdir(parents=True)
            (source / "SKILL.md").write_text("# Source\n\nRoute source artifacts.\n", encoding="utf-8")
            (target / "SKILL.md").write_text("# Target\n\nRoute target artifacts.\n", encoding="utf-8")
            initial, _ = scan_roots([root])
            source_hash = next(record.source_hash for record in initial if record.name == "Source")
            proposal_path = Path(tmp) / "proposals.json"
            proposal_path.write_text(
                json.dumps(
                    {
                        "proposals": [
                            {"skill": "Source", "field": "aliases", "value": ["Source Alias"], "source_hash": source_hash, "source_span": "1:1", "confidence": 0.9, "status": "approved"},
                            {"skill": "Source", "field": "requires", "value": ["Target"], "source_hash": source_hash, "source_span": "2:2", "confidence": 0.9, "status": "approved"},
                            {"skill": "Source", "field": "package", "value": "automation-ops", "source_hash": source_hash, "source_span": "3:3", "confidence": 0.9, "status": "approved"},
                            {"skill": "Source", "field": "network_access", "value": True, "source_hash": source_hash, "source_span": "4:4", "confidence": 0.9, "status": "approved"},
                            {"skill": "Source", "field": "default_routable", "value": False, "source_hash": source_hash, "source_span": "5:5", "confidence": 0.9, "status": "approved"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            approved, candidates, warnings = load_enrichment([proposal_path])
            self.assertEqual(warnings, [])
            records, _ = scan_roots([root], enrichment=approved)
            source_record = next(record for record in records if record.name == "Source")
            self.assertIn("Source Alias", source_record.aliases)
            self.assertEqual(source_record.requires, [])
            self.assertNotEqual(source_record.package, "automation-ops")
            self.assertIsNone(source_record.network_access)
            self.assertTrue(source_record.default_routable)
            self.assertTrue(any(item["field"] == "requires" for item in candidates))
            self.assertTrue(any(item["field"] == "package" for item in candidates))
            self.assertFalse(any(edge.relation == "requires" for edge in build_skill_edges(records)))
            direct, direct_warnings = scan_roots(
                [root],
                enrichment={
                    "source": [
                        {
                            "field": "package",
                            "value": "automation-ops",
                            "source_hash": source_hash,
                            "source_span": "6:6",
                            "confidence": 1.0,
                            "status": "approved",
                        }
                    ]
                },
            )
            self.assertNotEqual(next(item for item in direct if item.name == "Source").package, "automation-ops")
            self.assertTrue(any("Protected enrichment field" in warning for warning in direct_warnings))

    def test_direct_enrichment_api_requires_approved_audited_proposals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            skill = root / "router"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("# Router\n\nRoute local skills.\n", encoding="utf-8")
            initial, _ = scan_roots([root])
            record = initial[0]
            base = {
                "field": "aliases", "value": ["pending-alias"], "source_hash": record.source_hash,
                "source_span": "1:1", "confidence": 0.9,
            }
            invalid = [
                {**base, "status": "pending"},
                {**base, "status": "rejected"},
                {**base, "status": "approved", "confidence": float("nan")},
                {**base, "status": "approved", "source_span": ""},
            ]
            for proposal in invalid:
                with self.subTest(proposal=proposal):
                    records, warnings = scan_roots([root], enrichment={"router": [proposal]})
                    self.assertEqual(records[0].aliases, [])
                    self.assertTrue(warnings)
            approved = {**base, "status": "approved", "value": ["approved-alias"]}
            records, warnings = scan_roots([root], enrichment={"router": [approved]})
            self.assertEqual(warnings, [])
            self.assertEqual(records[0].aliases, ["approved-alias"])

    def test_agents_skills_root_is_rejected_and_symlinked_skill_is_not_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            agents = base / ".agents" / "skills"
            agents.mkdir(parents=True)
            (agents / "SKILL.md").write_text("# Forbidden\n\nMust not be scanned.\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Refusing to scan .agents/skills"):
                scan_roots([agents])
            parent_records, _ = scan_roots([base])
            self.assertEqual(parent_records, [])

            allowed = base / "allowed"
            outside = base / "outside"
            allowed.mkdir()
            outside.mkdir()
            (outside / "SKILL.md").write_text("# Outside\n\nMust not cross root.\n", encoding="utf-8")
            try:
                os.symlink(outside, allowed / "linked", target_is_directory=True)
            except OSError:
                return
            records, _ = scan_roots([allowed])
            self.assertEqual(records, [])

    def test_profile_compatibility_is_hard_gate(self) -> None:
        record = SkillRecord(
            id="windows", name="Windows Builder", source_path="synthetic/windows", detected_files=["SKILL.md"],
            description="Build a Windows desktop application.", when_to_use="Use for Windows builds.",
            compatibility="windows", package="coding-workflow", confidence=0.9,
        )
        blocked = route_records("use Windows Builder", [record], strategy="hybrid", profile="linux").to_dict()
        self.assertEqual(blocked["decision"]["type"], "continue_without_skill")
        self.assertEqual(blocked["excluded_candidates"][0]["reason_code"], "compatibility does not match routing profile")
        allowed = route_records("use Windows Builder", [record], strategy="hybrid", profile="windows").to_dict()
        self.assertEqual(allowed["primary"]["id"], "windows")
        mixed = SkillRecord(
            id="windows-python", name="Windows Python Builder", source_path="synthetic/windows-python", detected_files=["SKILL.md"],
            description="Build a Windows Python application.", when_to_use="Use for Windows Python builds.",
            compatibility="windows python", package="coding-workflow", confidence=0.9,
        )
        wrong_os = route_records(
            "use Windows Python Builder", [mixed], strategy="hybrid", profile="linux python"
        ).to_dict()
        self.assertEqual(wrong_os["decision"]["type"], "continue_without_skill")

    def test_schema_01_reads_with_warning_but_strict_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            vault = Path(tmp) / "vault"
            skill = root / "router"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("# Router\n\nRoute local skills.\n", encoding="utf-8")
            records, warnings = scan_roots([root])
            generate_vault(vault, records, [str(root)], warnings)
            index = vault / "00_Router" / "routing-index.json"
            payload = json.loads(index.read_text(encoding="utf-8"))
            payload["schema_version"] = "0.1"
            index.write_text(json.dumps(payload), encoding="utf-8")
            (vault / ".obsidian-skill-router" / "managed-files.json").unlink()
            ok, errors = validate_vault(vault)
            self.assertTrue(ok, errors)
            ok, errors = validate_vault(vault, strict=True)
            self.assertFalse(ok)
            self.assertTrue(any("0.1" in error for error in errors))
            decision = route_vault(vault, "use Router", strategy="hybrid").to_dict()
            self.assertTrue(any("deprecated" in warning for warning in decision["warnings"]))

    def test_candidate_edges_do_not_change_graph_route(self) -> None:
        records = [
            SkillRecord("a", "Alpha Router", "a", ["SKILL.md"], description="Route alpha requests.", when_to_use="Use for alpha.", package="coding-workflow", confidence=0.9),
            SkillRecord("b", "Beta Router", "b", ["SKILL.md"], description="Route beta requests.", when_to_use="Use for beta.", package="coding-workflow", confidence=0.9),
        ]
        candidate = GraphEdge("a", "b", "related", "candidate", 1.0, "mutual-knn", "candidate", "test")
        lexical = route_records("use Alpha Router", records, strategy="hybrid").to_dict()
        graph = route_records("use Alpha Router", records, strategy="hybrid-graph", edges=[candidate]).to_dict()
        self.assertEqual(lexical["primary"]["id"], graph["primary"]["id"])

    def test_graph_shadow_is_noop_without_confirmed_propagation_and_caps_lift(self) -> None:
        records = [
            SkillRecord(
                "alpha", "Alpha Router", "a", ["SKILL.md"],
                description="Route graph audit reports.", when_to_use="Use for graph audit reports.",
                package="coding-workflow", confidence=0.9,
            ),
            SkillRecord(
                "beta", "Beta Router", "b", ["SKILL.md"],
                description="Route graph audit reports.", when_to_use="Use for graph audit reports.",
                package="coding-workflow", confidence=0.9,
            ),
        ]
        lexical = rank_records("graph audit report", records, strategy="hybrid-graph")
        no_graph = apply_graph_shadow(lexical, [])
        self.assertEqual(no_graph.scored_records, lexical.scored_records)
        candidate = GraphEdge(
            "alpha", "beta", "workflow_next", "candidate", 1.0,
            "approved-enrichment", "candidate", "candidate evidence",
        )
        self.assertEqual(apply_graph_shadow(lexical, [candidate]).scored_records, lexical.scored_records)
        confirmed = GraphEdge(
            "alpha", "beta", "workflow_next", "confirmed", 1.0,
            "explicit-metadata", "confirmed", "alpha output -> beta input",
        )
        lifted = apply_graph_shadow(lexical, [confirmed])
        before = {record.id: score for score, record in lexical.scored_records}
        after = {record.id: score for score, record in lifted.scored_records}
        self.assertGreater(after["beta"], before["beta"])
        self.assertTrue(all(after[skill_id] <= score * 1.08 + 1e-12 for skill_id, score in before.items()))

    def test_missing_explicit_skill_cannot_be_filled_by_name_similarity(self) -> None:
        records = [
            SkillRecord(
                "playwright", "Playwright", "synthetic/playwright", ["SKILL.md"],
                description="Automate a browser and inspect web pages.", when_to_use="Use for browser automation.",
                package="coding-workflow", confidence=0.9,
            ),
            SkillRecord(
                "obsidian", "obsidian-markdown", "synthetic/obsidian", ["SKILL.md"],
                description="Write Obsidian markdown notes.", when_to_use="Use for Obsidian markdown.",
                package="memory-knowledge", confidence=0.9,
            ),
        ]
        single = route_records("use pdf", records, strategy="hybrid").to_dict()
        self.assertEqual(single["reason_code"], "explicit_skill_not_found")
        self.assertIsNone(single["primary"])
        plan = route_records("use pdf then use obsidian-markdown", records, strategy="hybrid").to_dict()
        self.assertEqual(plan["decision"]["type"], "clarify_required")
        self.assertEqual(plan["reason_code"], "explicit_skill_not_found")
        self.assertEqual(plan["plan"], [])

    def test_natural_use_phrase_is_not_misread_as_a_skill_name(self) -> None:
        records = [
            SkillRecord(
                "resume", "Resume Optimizer", "synthetic/resume", ["SKILL.md"],
                description="Rewrite a resume for a role using a job description.",
                when_to_use="Use a JD to tailor and rewrite a resume.",
                aliases=["resume-optimizer"], package="career-resume", confidence=0.9,
            )
        ]
        decision = route_records(
            "Use this JD to rewrite my resume for the role", records, strategy="hybrid"
        ).to_dict()
        self.assertEqual(decision["primary"]["id"], "resume")
        self.assertNotEqual(decision["reason_code"], "explicit_skill_not_found")

    def test_plan_cycle_and_large_greedy_plan(self) -> None:
        records = [
            SkillRecord(
                str(index), f"Stage {index}", str(index), ["SKILL.md"],
                description=f"Perform unique stage {index}.",
                when_to_use=f"Use for stage {index}.", package="coding-workflow",
                confidence=0.9, risk_level="low", action_risk="low",
            )
            for index in range(9)
        ]
        cycle = [
            GraphEdge("0", "1", "workflow_next", "explicit", 1.0, "explicit", "confirmed", "0 -> 1"),
            GraphEdge("1", "0", "workflow_next", "explicit", 1.0, "explicit", "confirmed", "1 -> 0"),
        ]
        blocked = route_records("use Stage 0 then use Stage 1", records, strategy="hybrid", edges=cycle).to_dict()
        self.assertEqual(blocked["reason_code"], "workflow_cycle")
        query = " then ".join(f"use Stage {index}" for index in range(9))
        plan = route_records(query, records, strategy="hybrid").to_dict()
        self.assertEqual(plan["decision"]["type"], "skill_plan")
        self.assertEqual(len(plan["plan"]), 9)

    def test_agent_first_hybrid_release_floor(self) -> None:
        root = Path("tests") / "fixtures" / "eval_hard_skills"
        cases = Path("tests") / "fixtures" / "eval_agent_first_redteam_cases.json"
        records, warnings = scan_roots([root])
        result = run_agent_first_eval_on_records(records, warnings, cases, strategy="hybrid")
        metrics = result["metrics"]
        self.assertGreaterEqual(metrics["primary_skill_precision_at_1"], 0.85)
        self.assertGreaterEqual(metrics["candidate_recall_at_3"], 0.90)
        self.assertLessEqual(metrics["false_activation_rate"], 0.01)
        self.assertEqual(metrics["high_risk_review_required_rate"], 1.0)
        self.assertEqual(metrics["constraint_respect_rate"], 1.0)
        graph_result = run_agent_first_eval_on_records(records, warnings, cases, strategy="hybrid-graph")
        self.assertEqual(graph_result["metrics"]["graph_incremental_top3_gain"], 0.0)

    def test_gate_config_rejects_na_and_failed_thresholds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gates = Path(tmp) / "gates.json"
            gates.write_text(
                json.dumps({"metrics": {"candidate_recall_at_3": {"min": 0.9}, "false_activation_rate": {"max": 0.01}}}),
                encoding="utf-8",
            )
            _enforce_gates(
                {"metrics": {"candidate_recall_at_3": 0.95, "false_activation_rate": 0.0}},
                gates,
            )
            with self.assertRaisesRegex(ValueError, "evaluation gates failed"):
                _enforce_gates(
                    {"metrics": {"candidate_recall_at_3": 0.5, "false_activation_rate": None}},
                    gates,
                )

    def test_ambiguity_prediction_does_not_receive_fixture_labels(self) -> None:
        import inspect

        records = [
            SkillRecord(
                "alpha", "Alpha", "a", ["SKILL.md"], description="Review a project report.",
                package="coding-workflow", confidence=0.9,
            ),
            SkillRecord(
                "beta", "Beta", "b", ["SKILL.md"], description="Review a project report.",
                package="research-writing", confidence=0.9,
            ),
        ]
        ranked = route_records("review a project report", records, strategy="legacy").package_candidates
        self.assertGreaterEqual(len(ranked), 2)
        from obsidian_skill_router.routing import rank_records

        result = rank_records("review a project report", records, strategy="legacy")
        prediction = _ambiguous_reasons(result)
        self.assertTrue(prediction)
        self.assertEqual(list(inspect.signature(_ambiguous_reasons).parameters), ["result"])

    def test_json_mode_wraps_argument_errors_in_one_envelope(self) -> None:
        with patch("sys.stdout", new_callable=io.StringIO) as stdout, patch("sys.stderr", new_callable=io.StringIO) as stderr:
            exit_code = main(["--json", "route", "--vault", "missing"])
        self.assertEqual(exit_code, 1)
        lines = [line for line in stdout.getvalue().splitlines() if line.strip()]
        self.assertEqual(len(lines), 1)
        payload = json.loads(lines[0])
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["command"], "route")
        self.assertEqual(stderr.getvalue(), "")

    def test_publish_failure_restores_previous_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            vault = Path(tmp) / "vault"
            skill = root / "router"
            skill.mkdir(parents=True)
            source = skill / "SKILL.md"
            source.write_text("# Router\n\nRoute local capabilities.\n", encoding="utf-8")
            records, warnings = scan_roots([root])
            generate_vault(vault, records, [str(root)], warnings)
            index = vault / "00_Router" / "routing-index.json"
            before = index.read_bytes()
            source.write_text("# Router\n\nRoute changed capabilities.\n", encoding="utf-8")
            changed, changed_warnings = scan_roots([root])

            from obsidian_skill_router import generate as generate_module

            real_replace = generate_module.os.replace
            calls = 0

            def fail_once(source_path: object, destination_path: object) -> None:
                nonlocal calls
                calls += 1
                if calls == 3:
                    raise OSError("injected publish failure")
                real_replace(source_path, destination_path)

            with patch("obsidian_skill_router.generate.os.replace", side_effect=fail_once):
                with self.assertRaisesRegex(OSError, "injected publish failure"):
                    generate_vault(vault, changed, [str(root)], changed_warnings)
            self.assertEqual(index.read_bytes(), before)

    def test_generated_markdown_neutralizes_untrusted_skill_markup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            vault = Path(tmp) / "vault"
            skill = root / "hostile"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\n"
                "name: Hostile [[outside]]\n"
                "description: |\n"
                "  ![](https://attacker.invalid/pixel)\n"
                "  <img src=https://attacker.invalid/html>\n"
                "  # Forged Heading\n"
                "---\n\n"
                "# Hostile\n\nUse for hostile rendering tests.\n",
                encoding="utf-8",
            )
            records, warnings = scan_roots([root])
            generate_vault(vault, records, [str(root)], warnings)
            card = (
                vault / "01_Packages" / records[0].package / "skills" / f"{records[0].id}.md"
                if records[0].package
                else vault / "04_Inbox" / "unclassified" / f"{records[0].id}.md"
            )
            text = card.read_text(encoding="utf-8")
            self.assertNotIn("![](https://attacker.invalid/pixel)", text)
            self.assertNotIn("<img", text)
            self.assertNotIn("\n# Forged Heading", text)
            self.assertIn(r"\!&#91;&#93;", text)
            self.assertIn("&lt;img", text)

    def test_malicious_managed_manifest_cannot_escape_vault(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            vault = Path(tmp) / "vault"
            skill = root / "router"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("# Router\n\nRoute local capabilities.\n", encoding="utf-8")
            records, warnings = scan_roots([root])
            generate_vault(vault, records, [str(root)], warnings)
            victim = Path(tmp).parent / "router-managed-manifest-victim.txt"
            victim.write_text("preserve", encoding="utf-8")
            self.addCleanup(lambda: victim.unlink(missing_ok=True))
            digest = hashlib.sha256(victim.read_bytes()).hexdigest()
            manifest = vault / ".obsidian-skill-router" / "managed-files.json"
            manifest.write_text(
                json.dumps({"schema_version": "0.2", "files": [{"path": "../../router-managed-manifest-victim.txt", "sha256": digest}]}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "Unsafe managed path"):
                generate_vault(vault, records, [str(root)], warnings)
            self.assertEqual(victim.read_text(encoding="utf-8"), "preserve")

    def test_vault_must_not_overlap_read_only_skill_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            skill = root / "router"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("# Router\n\nRoute local capabilities.\n", encoding="utf-8")
            records, warnings = scan_roots([root])
            with self.assertRaisesRegex(ValueError, "must not overlap"):
                generate_vault(root, records, [str(root)], warnings)
            with self.assertRaisesRegex(ValueError, "must not overlap"):
                generate_vault(root / "generated-vault", records, [str(root)], warnings)
            self.assertFalse((root / "00_Router").exists())

    def test_modified_managed_file_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            vault = Path(tmp) / "vault"
            skill = root / "router"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("# Router\n\nRoute local capabilities.\n", encoding="utf-8")
            records, warnings = scan_roots([root])
            generate_vault(vault, records, [str(root)], warnings)
            card = vault / "00_Router" / "Skill Package Map.md"
            card.write_text("user edit\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "modified managed file"):
                generate_vault(vault, records, [str(root)], warnings)
            self.assertEqual(card.read_text(encoding="utf-8"), "user edit\n")

    def test_validate_rejects_all_registry_path_traversal_without_reading_outside(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            vault = Path(tmp) / "vault"
            skill = root / "router"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("# Router\n\nRoute local capabilities.\n", encoding="utf-8")
            records, warnings = scan_roots([root])
            generate_vault(vault, records, [str(root)], warnings)

            index = vault / "00_Router" / "routing-index.json"
            payload = json.loads(index.read_text(encoding="utf-8"))
            payload["skills"][0]["card_path"] = "../../outside-card.md"
            payload["skills"][0]["source_relative_path"] = "../../outside-source"
            payload["skills"][0]["package"] = "../outside-package"
            index.write_text(json.dumps(payload), encoding="utf-8")

            manifest = vault / ".obsidian-skill-router" / "managed-files.json"
            managed = json.loads(manifest.read_text(encoding="utf-8"))
            managed["files"].append({"path": "../../outside-managed", "sha256": "0" * 64})
            manifest.write_text(json.dumps(managed), encoding="utf-8")
            map_path = vault / "00_Router" / "Skill Package Map.md"
            map_path.write_text(map_path.read_text(encoding="utf-8") + "\n[[../../outside-link]]\n", encoding="utf-8")

            ok, errors = validate_vault(vault, strict=True, check_sources=True)
            self.assertFalse(ok)
            joined = "\n".join(errors)
            self.assertIn("Unsafe package name", joined)
            self.assertIn("Unsafe skill card path", joined)
            self.assertIn("Unsafe source path", joined)
            self.assertIn("Unsafe managed file path", joined)
            self.assertIn("Unsafe wikilink", joined)

    def test_managed_publish_rejects_symlinked_output_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skills"
            vault = Path(tmp) / "vault"
            outside = Path(tmp) / "outside"
            skill = root / "router"
            skill.mkdir(parents=True)
            outside.mkdir()
            vault.mkdir()
            (skill / "SKILL.md").write_text("# Router\n\nRoute local capabilities.\n", encoding="utf-8")
            try:
                os.symlink(outside, vault / "01_Packages", target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlinks unavailable: {exc}")
            records, warnings = scan_roots([root])
            with self.assertRaisesRegex(ValueError, "symlink"):
                generate_vault(vault, records, [str(root)], warnings)
            self.assertEqual(list(outside.iterdir()), [])

    def test_2000_skill_hot_route_p95_under_100_ms(self) -> None:
        records = [
            SkillRecord(
                str(index), f"Synthetic Skill {index}", str(index), ["SKILL.md"],
                description=f"Process synthetic artifact {index} into a local report.",
                when_to_use=f"Use for synthetic artifact {index}.", package="coding-workflow", confidence=0.8,
            )
            for index in range(2000)
        ]
        route_records("process synthetic artifact 0", records, strategy="hybrid")
        timings = []
        for index in range(20):
            started = time.perf_counter()
            route_records(f"process synthetic artifact {index * 17}", records, strategy="hybrid")
            timings.append((time.perf_counter() - started) * 1000)
        p95 = sorted(timings)[18]
        self.assertLessEqual(p95, 100.0, {"p95_ms": p95, "median_ms": statistics.median(timings)})


if __name__ == "__main__":
    unittest.main()
