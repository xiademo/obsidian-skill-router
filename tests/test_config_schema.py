from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from obsidian_skill_router.config import load_config


class ConfigSchemaTests(unittest.TestCase):
    def test_nested_json_schema_is_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "router.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": "0.2",
                        "skills_roots": ["C:/skills"],
                        "vault": "C:/vault",
                        "discovery": {"max_depth": 4, "legacy_readme_skills": False},
                        "routing": {"package_rules": {"coding-workflow": ["code"]}},
                        "graph": {"max_workflow_next_per_source": 2},
                        "policy": {"risk_keywords": {"high": ["run command"]}},
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(str(path))

            self.assertEqual(config["max_depth"], 4)
            self.assertFalse(config["legacy_readme_skills"])
            self.assertEqual(config["package_rules"], {"coding-workflow": ["code"]})
            self.assertEqual(config["risk_keywords"], {"high": ["run command"]})
            self.assertEqual(config["max_workflow_next_per_source"], 2)
            self.assertNotIn("_warnings", config)

    def test_unknown_or_malformed_config_fails_closed(self) -> None:
        invalid = [
            {"schema_version": "0.3"},
            {"unknown": True},
            {"discovery": {"max_depth": "6"}},
            {"routing": {"package_rules": {"coding-workflow": {"bad": "shape"}}}},
            {"graph": {"enabled": True}},
            {"policy": {"risk_keywords": {"high": []}}},
        ]
        for payload in invalid:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "router.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_config(str(path))

    def test_legacy_json_and_yaml_are_marked_for_migration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            json_path = root / "legacy.json"
            json_path.write_text(
                json.dumps({"package_rules": {"coding-workflow": ["code"]}}),
                encoding="utf-8",
            )
            yaml_path = root / "legacy.yaml"
            yaml_path.write_text(
                "package_rules:\n  coding-workflow: [code, test]\n",
                encoding="utf-8",
            )

            json_config = load_config(str(json_path))
            yaml_config = load_config(str(yaml_path))

            self.assertTrue(any("deprecated" in item for item in json_config["_warnings"]))
            self.assertTrue(any("legacy" in item.lower() for item in yaml_config["_warnings"]))

    def test_nested_yaml_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "router.yaml"
            path.write_text("discovery:\n  max_depth: [6]\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "only in JSON"):
                load_config(str(path))


if __name__ == "__main__":
    unittest.main()
