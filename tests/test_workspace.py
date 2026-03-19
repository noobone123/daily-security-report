from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from skill_lib import ValidationError, bootstrap_planning, format_source_toml, load_all_sources, load_workspace, remove_source_block, validate_workspace


class WorkspaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]

    def test_bootstrap_templates_create_valid_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            templates_dir = self.repo_root / "skills" / "daily-security-digest" / "templates"
            bootstrap_payload = bootstrap_planning(root, templates_dir)
            self.assertEqual(len(bootstrap_payload["created"]), 3)

            payload = validate_workspace(root)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["sources"], 0)
            self.assertEqual(payload["enabled_sources"], 0)

    def test_workspace_rejects_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_workspace(root)
            (root / "planning" / "report-style.md").write_text(
                "---\nmode: invalid\n---\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValidationError, "frontmatter"):
                load_workspace(root)

    def test_source_requires_all_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_workspace(root)
            (root / "planning" / "sources.toml").write_text(
                '[[sources]]\nid = "demo-source"\ntitle = "Demo Source"\nenabled = true\nnotes = "No kind."\nfetch.url = "https://example.com/feed.xml"\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValidationError, "missing 'kind'"):
                load_workspace(root)

    def test_format_source_toml_rss(self) -> None:
        block = format_source_toml(
            source_id="my-blog-feed",
            title="My Blog Feed",
            kind="rss",
            enabled=True,
            notes="Auto-detected RSS.",
            fetch={"url": "https://example.com/feed.xml"},
        )
        self.assertIn('id = "my-blog-feed"', block)
        self.assertIn('kind = "rss"', block)
        self.assertIn("enabled = true", block)
        self.assertIn('fetch.url = "https://example.com/feed.xml"', block)

    def test_format_source_toml_rejects_bad_kind(self) -> None:
        with self.assertRaises(ValidationError):
            format_source_toml(
                source_id="bad",
                title="Bad",
                kind="invalid",
                fetch={"url": "https://x.com"},
            )

    def test_format_source_toml_roundtrips_through_tomllib(self) -> None:
        import tomllib

        block = format_source_toml(
            source_id="rt-test",
            title='Title with "quotes"',
            kind="web",
            fetch={"url": "https://example.com"},
        )
        data = tomllib.loads(block)
        self.assertEqual(data["sources"][0]["id"], "rt-test")
        self.assertEqual(data["sources"][0]["title"], 'Title with "quotes"')

    def test_remove_source_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_workspace(root)
            sources_path = root / "planning" / "sources.toml"
            removed = remove_source_block(sources_path, "demo-source")
            self.assertTrue(removed)
            text = sources_path.read_text(encoding="utf-8")
            self.assertNotIn("demo-source", text)

    def test_remove_source_block_returns_false_for_missing_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_workspace(root)
            sources_path = root / "planning" / "sources.toml"
            removed = remove_source_block(sources_path, "nonexistent")
            self.assertFalse(removed)

    def test_empty_sources_toml_returns_empty_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sources.toml"
            path.write_text("# No sources yet.\n", encoding="utf-8")
            sources = load_all_sources(path)
            self.assertEqual(sources, [])

    def _write_valid_workspace(self, root: Path) -> None:
        (root / "planning").mkdir(parents=True, exist_ok=True)
        (root / "planning" / "report-style.md").write_text(
            "# Report Style\n\n## Audience\n\nAnalyst.\n\n## Language\n\nEnglish.\n\n## Output Format\n\nMarkdown.\n\n## Extra Instructions\n\nKeep it short.\n",
            encoding="utf-8",
        )
        (root / "planning" / "sources.toml").write_text(
            '[[sources]]\nid = "demo-source"\ntitle = "Demo Source"\nkind = "rss"\nenabled = true\nnotes = "Example source."\nfetch.url = "https://example.com/feed.xml"\n',
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
