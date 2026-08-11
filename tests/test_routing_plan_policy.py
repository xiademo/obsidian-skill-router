from __future__ import annotations

import unittest

from obsidian_skill_router.models import GraphEdge, SkillRecord
from obsidian_skill_router.graph import build_skill_edges
from obsidian_skill_router.routing import _split_task_v2, apply_graph_shadow, rank_records, route_records


def _skill(skill_id: str, name: str, **overrides: object) -> SkillRecord:
    values: dict[str, object] = {
        "id": skill_id,
        "name": name,
        "source_path": f"synthetic/{skill_id}",
        "detected_files": ["SKILL.md"],
        "description": f"Perform the {name} stage.",
        "when_to_use": f"Use for {name} work.",
        "package": "coding-workflow",
        "confidence": 0.9,
        "risk_level": "low",
        "action_risk": "low",
    }
    values.update(overrides)
    return SkillRecord(**values)  # type: ignore[arg-type]


def _requires(source: str, target: str) -> GraphEdge:
    return GraphEdge(
        source,
        target,
        "requires",
        "explicit dependency",
        1.0,
        "explicit-metadata",
        "confirmed",
        f"{source} requires {target}",
    )


class RoutingPlanPolicyTests(unittest.TestCase):
    def test_bare_after_reverses_execution_order(self) -> None:
        records = [_skill("report", "Write Report"), _skill("research", "Research Sources")]

        result = route_records(
            "use Write Report after use Research Sources",
            records,
            strategy="hybrid",
        ).to_dict()

        self.assertEqual(result["decision"]["type"], "skill_plan")
        self.assertEqual([step["skill"]["id"] for step in result["plan"]], ["research", "report"])
        self.assertEqual([step["step"] for step in result["plan"]], [1, 2])
        self.assertEqual([step["original_step"] for step in result["plan"]], [1, 2])

    def test_no_dependency_preserves_user_clause_order(self) -> None:
        records = [_skill("alpha", "Alpha"), _skill("beta", "Beta")]

        result = route_records("use Beta then use Alpha", records, strategy="hybrid").to_dict()

        self.assertEqual(result["decision"]["type"], "skill_plan")
        self.assertEqual([step["skill"]["id"] for step in result["plan"]], ["beta", "alpha"])
        self.assertFalse(result["policy"]["dependency_order_adjusted"])

    def test_transitive_dependencies_precede_explicit_sequence(self) -> None:
        records = [
            _skill("report", "Write Report"),
            _skill("export", "PDF Export"),
            _skill("research", "Research Sources"),
            _skill("gather", "Gather Evidence"),
        ]

        result = route_records(
            "use Write Report then use PDF Export",
            records,
            strategy="hybrid",
            edges=[_requires("report", "research"), _requires("research", "gather")],
        ).to_dict()

        self.assertEqual(result["decision"]["type"], "skill_plan")
        self.assertEqual(
            [step["skill"]["id"] for step in result["plan"]],
            ["gather", "research", "report", "export"],
        )
        self.assertEqual([step["step"] for step in result["plan"]], [1, 2, 3, 4])
        self.assertEqual([step["original_step"] for step in result["plan"]], [None, None, 1, 2])

    def test_dangling_explicit_dependency_fails_closed(self) -> None:
        records = [
            _skill("report", "Write Report", requires=["Missing Research"]),
            _skill("export", "PDF Export"),
        ]

        result = route_records(
            "use Write Report then use PDF Export", records, strategy="hybrid"
        ).to_dict()

        self.assertEqual(result["decision"]["type"], "review_required")
        self.assertEqual(result["reason_code"], "missing_required_dependency")
        self.assertIn("Missing Research", result["evidence"][0])

    def test_incompatible_dependency_is_not_added_to_plan(self) -> None:
        records = [
            _skill("alpha", "Alpha"),
            _skill("export", "Export"),
            _skill("windows", "Windows Dependency", compatibility="windows", route_mode="profile-only"),
        ]

        result = route_records(
            "use Alpha then use Export",
            records,
            strategy="hybrid",
            profile="linux",
            edges=[_requires("alpha", "windows")],
        ).to_dict()

        self.assertEqual(result["decision"]["type"], "review_required")
        self.assertEqual(result["reason_code"], "dependency_policy_blocked")
        self.assertNotIn("windows", [step["skill"]["id"] for step in result["plan"]])

    def test_manual_dependency_is_included_only_as_review_plan(self) -> None:
        records = [
            _skill("alpha", "Alpha"),
            _skill("export", "Export"),
            _skill("manual", "Manual Dependency", route_mode="manual"),
        ]

        result = route_records(
            "use Alpha then use Export",
            records,
            strategy="hybrid",
            edges=[_requires("alpha", "manual")],
        ).to_dict()

        self.assertEqual(result["decision"]["type"], "review_required")
        self.assertEqual(result["reason_code"], "dependency_policy_review")
        self.assertEqual(
            [step["skill"]["id"] for step in result["plan"]],
            ["manual", "alpha", "export"],
        )

    def test_blocked_explicit_skill_is_not_replaced_by_similar_candidate(self) -> None:
        records = [
            _skill("windows", "Windows Builder", compatibility="windows"),
            _skill(
                "cross",
                "Cross Builder",
                description="Build a Windows or Linux application.",
                compatibility="linux",
            ),
        ]

        result = route_records(
            "use Windows Builder", records, strategy="hybrid", profile="linux"
        ).to_dict()

        self.assertEqual(result["decision"]["type"], "continue_without_skill")
        self.assertEqual(result["reason_code"], "explicit_skill_policy_blocked")
        self.assertIsNone(result["primary"])
        self.assertEqual(result["excluded_candidates"][0]["id"], "windows")

    def test_ambiguous_plan_step_requires_clarification(self) -> None:
        records = [
            _skill("alpha-one", "Alpha One", description="Process an alpha artifact."),
            _skill("alpha-two", "Alpha Two", description="Process an alpha artifact."),
            _skill("beta", "Beta"),
        ]

        result = route_records(
            "process alpha artifact then use Beta", records, strategy="hybrid"
        ).to_dict()

        self.assertEqual(result["decision"]["type"], "clarify_required")
        self.assertEqual(result["reason_code"], "plan_step_ambiguous")

    def test_chinese_reproduction_word_is_not_a_sequence_marker(self) -> None:
        self.assertEqual(_split_task_v2("再现 bug 并分析"), ["再现 bug 并分析"])

    def test_ambiguous_dependency_name_never_becomes_confirmed_edge_or_plan(self) -> None:
        records = [
            _skill("orchestrator", "Orchestrator", requires=["Shared"]),
            _skill("export", "Export"),
            _skill("trusted", "Shared"),
            _skill("shadow", "Shared"),
        ]

        edges = build_skill_edges(records)
        result = route_records(
            "use Orchestrator then use Export", records, strategy="hybrid", edges=edges
        ).to_dict()

        self.assertFalse(any(edge.relation == "requires" for edge in edges))
        self.assertEqual(result["decision"]["type"], "review_required")
        self.assertEqual(result["reason_code"], "ambiguous_required_dependency")
        self.assertIn("stable skill id", result["evidence"][0])

    def test_graph_shadow_subgraph_includes_zero_lexical_one_hop_nodes(self) -> None:
        records = [
            _skill("alpha", "Alpha", description="Create abc123xyz789 output."),
            _skill("beta", "Beta", description="Review abc123xyz789 output."),
            _skill(
                "bridge", "Zqxwv", description="Jmn pkf.", when_to_use="Zqxwv only.", package=""
            ),
        ]
        lexical = rank_records("abc123xyz789", records, strategy="hybrid-graph")
        self.assertNotIn("bridge", [record.id for _, record in lexical.scored_records])
        edges = [
            GraphEdge(
                "alpha", "bridge", "workflow_next", "explicit", 1.0,
                "explicit-metadata", "confirmed", "alpha -> bridge",
            ),
            GraphEdge(
                "bridge", "beta", "workflow_next", "explicit", 1.0,
                "explicit-metadata", "confirmed", "bridge -> beta",
            ),
        ]

        without_full_registry = apply_graph_shadow(lexical, edges)
        with_full_registry = apply_graph_shadow(lexical, edges, records)
        without = {record.id: score for score, record in without_full_registry.scored_records}
        with_bridge = {record.id: score for score, record in with_full_registry.scored_records}

        self.assertEqual(without, {record.id: score for score, record in lexical.scored_records})
        self.assertGreater(with_bridge["beta"], without["beta"])


if __name__ == "__main__":
    unittest.main()
