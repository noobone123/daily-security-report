from __future__ import annotations

import tempfile
import unittest
import shutil
from pathlib import Path

from skill_lib import ValidationError, bootstrap_planning, format_source_toml, load_all_sources, load_workspace, read_workspace_config, remove_source_block, validate_workspace, write_workspace_config


class WorkspaceTest(unittest.TestCase):
    def test_bootstrap_templates_create_valid_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env = self._make_skill_env(Path(tmpdir))
            root = env["workspace"]
            write_workspace_config(env["config_path"], root)
            bootstrap_payload = bootstrap_planning(env["templates_dir"])
            self.assertEqual(len(bootstrap_payload["created"]), 3)
            self.assertEqual(bootstrap_payload["workspace"], str(root.resolve()))
            self.assertEqual(bootstrap_payload["workspace_config_path"], str(env["config_path"].resolve()))
            self.assertEqual(bootstrap_payload["planning_dir"], str((root / "planning").resolve()))
            self.assertEqual(bootstrap_payload["runs_dir"], str((root / "data" / "runs").resolve()))

            payload = validate_workspace(root)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["sources"], 0)
            self.assertEqual(payload["enabled_sources"], 0)

    def test_read_workspace_config_returns_configured_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env = self._make_skill_env(Path(tmpdir))
            root = env["workspace"]
            write_workspace_config(env["config_path"], root)

            resolved = read_workspace_config(env["config_path"])
            self.assertEqual(resolved.root, root.resolve())
            self.assertEqual(resolved.config_path, env["config_path"].resolve())

    def test_read_workspace_config_rejects_missing_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "missing.toml"
            with self.assertRaisesRegex(ValidationError, "Missing workspace config"):
                read_workspace_config(config_path)

    def test_read_workspace_config_rejects_relative_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config_path.write_text('workspace_root = "relative/path"\n', encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "absolute path"):
                read_workspace_config(config_path)

    def test_read_workspace_config_rejects_missing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            missing = Path(tmpdir) / "missing"
            config_path.write_text(f'workspace_root = "{missing}"\n', encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "does not exist"):
                read_workspace_config(config_path)

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

    def test_load_workspace_rejects_legacy_rss_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_valid_workspace(root)
            (root / "planning" / "sources.toml").write_text(
                '[[sources]]\n'
                'id = "legacy-rss"\n'
                'title = "Legacy RSS"\n'
                'kind = "rss"\n'
                'enabled = true\n'
                'notes = "Legacy feed."\n'
                'fetch.url = "https://example.com/feed.xml"\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValidationError, "RSS/Atom sources are no longer supported"):
                load_workspace(root)

    def test_format_source_toml_rejects_rss(self) -> None:
        with self.assertRaisesRegex(ValidationError, "RSS/Atom sources are no longer supported"):
            format_source_toml(
                source_id="my-blog-feed",
                title="My Blog Feed",
                kind="rss",
                enabled=True,
                notes="Auto-detected RSS.",
                fetch={"url": "https://example.com/feed.xml"},
            )

    def test_format_source_toml_rejects_bad_kind(self) -> None:
        with self.assertRaises(ValidationError):
            format_source_toml(
                source_id="bad",
                title="Bad",
                kind="invalid",
                fetch={"url": "https://x.com"},
            )

    def test_format_source_toml_rejects_x_home_kind(self) -> None:
        with self.assertRaises(ValidationError):
            format_source_toml(
                source_id="no-x-home",
                title="No X Home",
                kind="x_home",
                enabled=True,
                fetch={},
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
            '[[sources]]\nid = "demo-source"\ntitle = "Demo Source"\nkind = "web"\nenabled = true\nnotes = "Example source."\nfetch.url = "https://example.com/security"\n',
            encoding="utf-8",
        )

    def _make_skill_env(self, root: Path) -> dict[str, Path]:
        repo_root = Path(__file__).resolve().parents[1]
        skill_src = repo_root / "skills" / "daily-security-digest"
        skill_dir = root / "skills" / "daily-security-digest"
        templates_dir = skill_dir / "templates"
        workspace = root / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        shutil.copytree(skill_src / "templates", templates_dir)
        return {
            "skill_dir": skill_dir,
            "templates_dir": templates_dir,
            "config_path": skill_dir / "config.toml",
            "workspace": workspace,
        }


if __name__ == "__main__":
    unittest.main()
