from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from obsidian_skill_router.registry import load_routing_overlays
from obsidian_skill_router.scan import scan_roots


def _duplicate_named_root(base: Path) -> Path:
    root = base / "skills"
    for directory in (root / "trusted", root / "shadow"):
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            "---\nname: Shared\n---\n\n# Shared\n\nRoute shared work.\n",
            encoding="utf-8",
        )
    return root


class OverlayIdentityTests(unittest.TestCase):
    def test_name_overlay_rejects_ambiguous_duplicate_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = _duplicate_named_root(base)
            rules = base / "rules.json"
            rules.write_text(
                json.dumps(
                    {
                        "schema_version": "0.2",
                        "skills": [{"name": "Shared", "aliases": ["governed-alias"]}],
                    }
                ),
                encoding="utf-8",
            )
            overlays, _ = load_routing_overlays([rules])

            with self.assertRaisesRegex(ValueError, "Ambiguous name-targeted routing rule"):
                scan_roots([root], routing_overlays=overlays)

    def test_stable_id_overlay_changes_only_selected_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = _duplicate_named_root(base)
            initial, _ = scan_roots([root])
            trusted = next(record for record in initial if record.source_relative_path == "trusted")
            rules = base / "rules.json"
            rules.write_text(
                json.dumps(
                    {
                        "schema_version": "0.2",
                        "skills": [{"id": trusted.id, "aliases": ["trusted-alias"]}],
                    }
                ),
                encoding="utf-8",
            )
            overlays, _ = load_routing_overlays([rules])

            records, _ = scan_roots([root], routing_overlays=overlays)

            selected = next(record for record in records if record.id == trusted.id)
            shadow = next(record for record in records if record.id != trusted.id)
            self.assertEqual(selected.aliases, ["trusted-alias"])
            self.assertEqual(shadow.aliases, [])

    def test_root_relative_overlay_changes_only_selected_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = _duplicate_named_root(base)
            rules = base / "rules.json"
            rules.write_text(
                json.dumps(
                    {
                        "schema_version": "0.2",
                        "skills": [
                            {
                                "root_id": "skills",
                                "relative_path": "shadow",
                                "aliases": ["shadow-alias"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            overlays, _ = load_routing_overlays([rules])

            records, _ = scan_roots([root], routing_overlays=overlays)

            selected = next(record for record in records if record.source_relative_path == "shadow")
            trusted = next(record for record in records if record.source_relative_path == "trusted")
            self.assertEqual(selected.aliases, ["shadow-alias"])
            self.assertEqual(trusted.aliases, [])

    def test_overlay_schema_rejects_unknown_fields_and_duplicate_targets(self) -> None:
        invalid_payloads = [
            {"skills": [], "ignored": True},
            {"skills": [{"name": "Shared", "ignored": True}]},
            {"skills": [{"name": "Shared", "id": "shared-1"}]},
            {"skills": [{"name": "Shared"}, {"declared_name": "shared"}]},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "rules.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_routing_overlays([path])

    def test_unmatched_overlay_selector_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = _duplicate_named_root(base)
            rules = base / "rules.json"
            rules.write_text(
                json.dumps(
                    {
                        "schema_version": "0.2",
                        "skills": [{"id": "shared-deadbeef", "route_mode": "manual"}],
                    }
                ),
                encoding="utf-8",
            )
            overlays, _ = load_routing_overlays([rules])

            with self.assertRaisesRegex(ValueError, "did not match any Skill"):
                scan_roots([root], routing_overlays=overlays)

    def test_name_enrichment_rejects_ambiguous_duplicate_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = _duplicate_named_root(base)
            initial, _ = scan_roots([root])
            proposal = {
                "field": "aliases",
                "value": ["proposed-alias"],
                "source_hash": initial[0].source_hash,
                "source_span": "SKILL.md:1-4",
                "confidence": 0.9,
                "status": "approved",
            }

            with self.assertRaisesRegex(ValueError, "Ambiguous name-targeted enrichment"):
                scan_roots([root], enrichment={"shared": [proposal]})

    @unittest.skipIf(os.name == "nt", "case-distinct paths require a case-sensitive filesystem")
    def test_case_distinct_paths_have_distinct_ids_and_path_overlay_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "skills"
            for relative, name in (("Foo", "Shared"), ("foo", "Shared")):
                directory = root / relative
                directory.mkdir(parents=True)
                (directory / "SKILL.md").write_text(
                    f"---\nname: {name}\n---\n\n# {name}\n\nRoute shared work.\n",
                    encoding="utf-8",
                )
            initial, _ = scan_roots([root])
            self.assertEqual(len({record.id for record in initial}), 2)
            rules = base / "rules.json"
            rules.write_text(
                json.dumps(
                    {
                        "schema_version": "0.2",
                        "skills": [
                            {
                                "root_id": "skills",
                                "relative_path": "Foo",
                                "aliases": ["upper-path"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            overlays, _ = load_routing_overlays([rules])
            records, _ = scan_roots([root], routing_overlays=overlays)
            upper = next(record for record in records if record.source_relative_path == "Foo")
            lower = next(record for record in records if record.source_relative_path == "foo")
            self.assertEqual(upper.aliases, ["upper-path"])
            self.assertEqual(lower.aliases, [])


if __name__ == "__main__":
    unittest.main()
