from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import unittest

from obsidian_skill_router.cli import _parser
from obsidian_skill_router.config import load_config
from obsidian_skill_router.registry import load_enrichment, load_routing_overlays


ROOT = Path(__file__).resolve().parents[1]
LOCAL_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")


class DocumentationContractTests(unittest.TestCase):
    def test_public_documentation_links_resolve(self) -> None:
        documents = [
            ROOT / "README.md",
            ROOT / "README.zh-CN.md",
            ROOT / "docs" / "README.md",
            ROOT / "docs" / "CLI.md",
            ROOT / "docs" / "CONFIGURATION.md",
            ROOT / "examples" / "README.md",
        ]
        for document in documents:
            text = document.read_text(encoding="utf-8")
            for match in LOCAL_LINK.finditer(text):
                target = match.group(1).split("#", 1)[0]
                if not target or target.startswith(("http://", "https://", "mailto:")):
                    continue
                resolved = (document.parent / target).resolve()
                self.assertTrue(resolved.exists(), f"broken link in {document}: {target}")

    def test_cli_reference_covers_every_command(self) -> None:
        parser = _parser()
        subparsers = next(
            action for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        documented = (ROOT / "docs" / "CLI.md").read_text(encoding="utf-8")
        self.assertEqual(
            {"scan", "validate", "report", "route", "hook", "eval"},
            set(subparsers.choices),
        )
        for command in subparsers.choices:
            self.assertIn(f"## `{command}`", documented)

    def test_committed_examples_match_their_loaders(self) -> None:
        examples = ROOT / "examples"
        for path in examples.glob("*.json"):
            with self.subTest(path=path.name):
                self.assertIsInstance(json.loads(path.read_text(encoding="utf-8")), dict)

        config = load_config(str(examples / "config.example.json"))
        self.assertEqual(6, config["max_depth"])
        self.assertFalse(config["legacy_readme_skills"])

        overlays, warnings = load_routing_overlays(
            [examples / "routing-rules.example.json"]
        )
        self.assertTrue(overlays)
        self.assertEqual([], warnings)

        approved, candidates, warnings = load_enrichment(
            [examples / "enrichment-proposals.example.json"]
        )
        self.assertEqual({}, approved)
        self.assertEqual(1, len(candidates))
        self.assertEqual([], warnings)


if __name__ == "__main__":
    unittest.main()
