from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from obsidian_skill_router.eval import enforce_eval_gates, load_eval_cases, run_agent_first_eval_on_records


FIXTURES = Path("tests") / "fixtures"


class _Decision:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def to_dict(self) -> dict[str, object]:
        return self.payload


def _skill(name: str, package: str) -> dict[str, object]:
    return {"id": f"{name.lower().replace(' ', '-')}-id", "name": name, "package": package, "score": 10.0}


def _decision(
    decision_type: str,
    *,
    status: str,
    primary: dict[str, object] | None = None,
    alternatives: list[dict[str, object]] | None = None,
    plan: list[dict[str, object]] | None = None,
) -> _Decision:
    return _Decision(
        {
            "status": status,
            "primary": primary,
            "alternatives": alternatives or [],
            "plan": plan or [],
            "decision": {"type": decision_type, "reason_code": "test"},
            "related_packages": [],
            "constraint_detected": False,
            "budget": {"response_tokens_estimate": 20, "candidate_payload_tokens_estimate": 8},
            "reason": "synthetic decision",
        }
    )


class EvalCaseContractTests(unittest.TestCase):
    def test_loader_supports_full_contract_and_legacy_fixtures(self) -> None:
        cases = load_eval_cases(FIXTURES / "eval_contract_cases.json")
        self.assertEqual(cases[0].intent_family, "research-report-plan")
        self.assertEqual(cases[0].profile, "local")
        self.assertEqual(cases[0].required_plan_skills, ("Research", "Report"))
        self.assertEqual(cases[0].forbidden_skills, ("Delete Files",))
        self.assertEqual(cases[0].required_order, (("Research", "Report"),))
        self.assertFalse(cases[0].expected_review)

        for path in [
            FIXTURES / "eval_cases.json",
            FIXTURES / "eval_hard_cases.json",
            FIXTURES / "eval_cn_ambiguity_cases.json",
            FIXTURES / "eval_agent_first_redteam_cases.json",
            Path("examples") / "eval-cases.example.json",
        ]:
            with self.subTest(path=path):
                self.assertTrue(load_eval_cases(path))

    def test_loader_rejects_ambiguous_or_invalid_schema_values(self) -> None:
        base = {"id": "case-1", "query": "review code", "should_trigger": True}
        invalid_cases = [
            ({**base, "unknown": "field"}, "unknown fields"),
            ({**base, "should_trigger": "false"}, "must be a boolean"),
            ({**base, "expected_top3_contains": "Review"}, "must be a list"),
            ({**base, "expected_decision": "maybe"}, "expected_decision is invalid"),
            ({**base, "risk_expectation": "critical"}, "risk_expectation is invalid"),
            ({**base, "expected_review": None}, "must be a boolean"),
            ({**base, "importance": 1.5}, "importance must be an integer"),
            (
                {**base, "required_plan_skills": ["A", "B"], "required_order": [["A", "C"]]},
                "endpoints must appear",
            ),
            ({**base, "required_plan_skills": ["A"]}, "at least two"),
            (
                {**base, "required_plan_skills": ["A", "B"], "required_order": [["A", "B"], ["B", "A"]]},
                "must be acyclic",
            ),
            (
                {**base, "required_plan_skills": ["A", "B"], "expected_decision": "recommend"},
                "plan requirements conflict",
            ),
            ({**base, "should_trigger": False, "expected_decision": "recommend"}, "inactive case conflicts"),
            (
                {**base, "expected_review": True, "expected_decision": "recommend"},
                "expected_review conflicts",
            ),
            ({**base, "query": "one", "user_task": "two"}, "conflict"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cases.json"
            for item, error in invalid_cases:
                with self.subTest(error=error):
                    path.write_text(json.dumps([item]), encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, error):
                        load_eval_cases(path)

            path.write_text(json.dumps([base, base]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must be unique"):
                load_eval_cases(path)

            path.write_text('{"cases": [], "cases": []}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate object key"):
                load_eval_cases(path)

            path.write_text('{"schema_version":"0.2","cases":[],"extra":true}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unknown top-level"):
                load_eval_cases(path)

    def test_agent_first_uses_structured_decisions_and_scores_plans(self) -> None:
        research = _skill("Research", "research-writing")
        report = _skill("Report", "research-writing")
        secret_reader = _skill("Secret Reader", "safety-governance")
        redactor = _skill("Redactor", "safety-governance")
        responses = [
            _decision(
                "skill_plan",
                status="recommend",
                primary=research,
                plan=[{"step": 1, "skill": research}, {"step": 2, "skill": report}],
            ),
            _decision(
                "review_required",
                status="review_required",
                primary=secret_reader,
                plan=[{"step": 1, "skill": secret_reader}, {"step": 2, "skill": redactor}],
            ),
            _decision(
                "clarify_required",
                status="continue_without_skill",
                alternatives=[_skill("Research", "research-writing"), _skill("Report", "research-writing")],
            ),
            _decision("continue_without_skill", status="continue_without_skill"),
        ]
        with patch("obsidian_skill_router.eval.route_records", side_effect=responses):
            result = run_agent_first_eval_on_records(
                [],
                [],
                FIXTURES / "eval_contract_cases.json",
                strategy="hybrid",
            )

        metrics = result["metrics"]
        self.assertEqual(metrics["decision_accuracy"], 1.0)
        self.assertEqual(metrics["clarification_recall"], 1.0)
        self.assertEqual(metrics["no_skill_recall"], 1.0)
        self.assertEqual(metrics["false_activation_rate"], 0.0)
        self.assertEqual(metrics["over_clarification_rate"], 0.0)
        self.assertEqual(metrics["candidate_recall_at_3"], 1.0)
        self.assertEqual(metrics["candidate_required_coverage_at_3"], 1.0)
        self.assertEqual(metrics["plan_skill_recall"], 1.0)
        self.assertEqual(metrics["plan_skill_precision"], 1.0)
        self.assertEqual(metrics["plan_order_validity"], 1.0)
        self.assertEqual(metrics["plan_review_accuracy"], 1.0)
        self.assertEqual(result["counts"]["intent_families"], 4)
        first = result["case_details"][0]
        self.assertEqual(first["actual_status"], "recommend")
        self.assertEqual(first["actual_decision"], "skill_plan")

    def test_clarification_is_not_counted_as_false_activation(self) -> None:
        payload = [{"id": "negative", "query": "unsupported task", "should_trigger": False}]
        with tempfile.TemporaryDirectory() as tmp:
            cases = Path(tmp) / "cases.json"
            cases.write_text(json.dumps(payload), encoding="utf-8")
            response = _decision("clarify_required", status="continue_without_skill")
            with patch("obsidian_skill_router.eval.route_records", return_value=response):
                result = run_agent_first_eval_on_records([], [], cases, strategy="hybrid")
        self.assertEqual(result["metrics"]["false_activation_rate"], 0.0)
        self.assertEqual(result["metrics"]["no_skill_recall"], 0.0)
        self.assertEqual(result["metrics"]["over_clarification_rate"], 1.0)

    def test_agent_first_rejects_duplicate_ids_across_sources(self) -> None:
        path = FIXTURES / "eval_contract_cases.json"
        with self.assertRaisesRegex(ValueError, "unique across"):
            run_agent_first_eval_on_records([], [], path, path, strategy="hybrid")


class EvalGateContractTests(unittest.TestCase):
    def test_gate_config_accepts_finite_min_max_and_strategy_scopes(self) -> None:
        result = {
            "strategies": {
                "hybrid": {"metrics": {"candidate_recall_at_3": 0.95, "false_activation_rate": 0.0}},
                "legacy": {"metrics": {"candidate_recall_at_3": 0.50, "false_activation_rate": 0.2}},
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gates.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": "0.2",
                        "strategies": {
                            "hybrid": {
                                "metrics": {
                                    "candidate_recall_at_3": {"min": 0.90, "max": 1.0},
                                    "false_activation_rate": {"max": 0.01},
                                }
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            enforce_eval_gates(result, path)

    def test_gate_config_rejects_unknown_nonfinite_and_invalid_rules(self) -> None:
        invalid = [
            ({"metrics": {"x": {"min": 0}}, "unknown": True}, "unknown fields"),
            ({"metrics": {"x": {"minimum": 0}}}, "only min and/or max"),
            ({"metrics": {"x": {"min": True}}}, "must be finite"),
            ({"metrics": {"x": {"min": 2, "max": 1}}}, "min greater than max"),
            ({"metrics": {}, "strategies": {}}, "exactly one"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gates.json"
            for payload, error in invalid:
                with self.subTest(error=error):
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, error):
                        enforce_eval_gates({"metrics": {"x": 1.0}}, path)

            path.write_text('{"metrics":{"x":{"min":NaN}}}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "non-finite"):
                enforce_eval_gates({"metrics": {"x": 1.0}}, path)

            path.write_text(json.dumps({"metrics": {"x": {"min": 0.0}}}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "evaluation gates failed"):
                enforce_eval_gates({"metrics": {"x": float("inf")}}, path)


if __name__ == "__main__":
    unittest.main()
