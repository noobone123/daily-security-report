from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from skill_lib import HttpClient, SourceSpec, TimeWindow, bootstrap_planning, fetch_raw_records, normalize_raw_records, write_workspace_config


class DistributionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.skill_dir = self.repo_root / "skills" / "daily-security-digest"
        self.templates_dir = self.skill_dir / "templates"

    def test_plugin_manifest_and_components_exist(self) -> None:
        manifest_path = self.repo_root / ".claude-plugin" / "plugin.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "daily-security-report")
        self.assertEqual(manifest["version"], "0.2.0")
        self.assertTrue((self.skill_dir / "SKILL.md").exists())
        for agent_name in ("source-resolver", "web-source-collector", "item-filter", "report-writer"):
            self.assertTrue((self.repo_root / "agents" / f"{agent_name}.md").exists())

    def test_claude_install_script_creates_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            claude_dir = Path(tmpdir) / ".claude"
            result = subprocess.run(
                ["bash", str(self.repo_root / "scripts" / "claude_install.sh"), "--claude-dir", str(claude_dir)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

            skill_link = claude_dir / "skills" / "daily-security-digest"
            self.assertTrue(skill_link.is_symlink())
            self.assertEqual(skill_link.resolve(), (self.repo_root / "skills" / "daily-security-digest").resolve())
            config_path = self.skill_dir / "config.toml"
            self.assertTrue(config_path.exists())
            config_text = config_path.read_text(encoding="utf-8")
            self.assertIn(str(self.repo_root.resolve()), config_text)

            for agent_name in ("source-resolver", "web-source-collector", "item-filter", "report-writer"):
                agent_link = claude_dir / "agents" / f"{agent_name}.md"
                self.assertTrue(agent_link.is_symlink())
                self.assertEqual(agent_link.resolve(), (self.repo_root / "agents" / f"{agent_name}.md").resolve())

    def test_bootstrap_planning_uses_skill_templates_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir()
            skill_dir = Path(tmpdir) / "skill"
            shutil.copytree(self.templates_dir, skill_dir / "templates")
            write_workspace_config(skill_dir / "config.toml", workspace)
            payload = bootstrap_planning(skill_dir / "templates")
            created = {Path(path).name for path in payload["created"]}
            self.assertEqual(created, {"sources.toml", "topics.md", "report-style.md"})

    def test_bootstrap_cli_reports_workspace_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "workspace"
            repo_root.mkdir()
            skill_dir = Path(tmpdir) / "skill"
            shutil.copytree(self.skill_dir / "scripts", skill_dir / "scripts")
            shutil.copytree(self.templates_dir, skill_dir / "templates")
            write_workspace_config(skill_dir / "config.toml", repo_root)
            script_path = skill_dir / "scripts" / "bootstrap_planning.py"

            result = subprocess.run(
                ["python3", str(script_path)],
                capture_output=True,
                text=True,
                check=False,
                cwd=tmpdir,
            )
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

            payload = json.loads(result.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["workspace"], str(repo_root.resolve()))
            self.assertEqual(payload["workspace_config_path"], str((skill_dir / "config.toml").resolve()))
            self.assertEqual(payload["planning_dir"], str((repo_root / "planning").resolve()))
            self.assertEqual(payload["runs_dir"], str((repo_root / "data" / "runs").resolve()))

    def test_claude_install_script_config_only_writes_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            claude_dir = Path(tmpdir) / ".claude"
            result = subprocess.run(
                ["bash", str(self.repo_root / "scripts" / "claude_install.sh"), "--claude-dir", str(claude_dir), "--config-only"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            self.assertFalse((claude_dir / "skills" / "daily-security-digest").exists())
            self.assertFalse((claude_dir / "agents" / "source-resolver.md").exists())
            config_path = self.skill_dir / "config.toml"
            self.assertTrue(config_path.exists())
            self.assertIn(str(self.repo_root.resolve()), config_path.read_text(encoding="utf-8"))

    def test_repo_has_single_canonical_agent_set(self) -> None:
        self.assertFalse((self.repo_root / ".claude" / "agents" / "source-resolver.md").exists())
        self.assertFalse((self.repo_root / ".claude" / "agents" / "web-source-collector.md").exists())
        self.assertFalse((self.repo_root / ".claude" / "agents" / "item-filter.md").exists())
        self.assertFalse((self.repo_root / ".claude" / "agents" / "report-writer.md").exists())

    def test_source_resolver_contract_documents_handle(self) -> None:
        text = (self.repo_root / "agents" / "source-resolver.md").read_text(encoding="utf-8")
        self.assertIn("fetch.handle", text)
        self.assertIn("github_feed", text)
        self.assertIn("x_home", text)
        self.assertIn("x.com/home", text)
        self.assertIn("@authenticated", text)
        self.assertNotIn("fetch.username", text)

    def test_docs_publish_github_feed_kind(self) -> None:
        readme = (self.repo_root / "README.md").read_text(encoding="utf-8")
        skill = (self.skill_dir / "SKILL.md").read_text(encoding="utf-8")
        template = (self.templates_dir / "sources.toml.example").read_text(encoding="utf-8")
        for text in (readme, skill, template):
            self.assertIn("github_feed", text)
            self.assertIn("x_home", text)
        self.assertIn("@authenticated", readme)
        self.assertIn("@authenticated", skill)
        self.assertIn("@authenticated", template)
        self.assertIn("X_USER_ACCESS_TOKEN", readme)
        self.assertIn("X_USER_ACCESS_TOKEN", skill)
        self.assertIn("X_USER_ACCESS_TOKEN", template)

    def test_readme_links_to_github_feed_setup_doc(self) -> None:
        readme = (self.repo_root / "README.md").read_text(encoding="utf-8")
        doc_path = self.repo_root / "docs" / "github-feed-setup.md"
        self.assertTrue(doc_path.exists())
        self.assertIn("docs/github-feed-setup.md", readme)

    def test_readme_links_to_x_home_setup_doc(self) -> None:
        readme = (self.repo_root / "README.md").read_text(encoding="utf-8")
        doc_path = self.repo_root / "docs" / "x-home-setup.md"
        self.assertTrue(doc_path.exists())
        self.assertIn("docs/x-home-setup.md", readme)
        self.assertNotIn("X_API_KEY", readme)
        self.assertNotIn("X_API_SECRET", readme)
        self.assertNotIn("X_ACCESS_TOKEN_SECRET", readme)

    def test_x_home_setup_doc_is_concise_and_bearer_focused(self) -> None:
        text = (self.repo_root / "docs" / "x-home-setup.md").read_text(encoding="utf-8")
        self.assertIn("本项目目前只需要一个环境变量", text)
        self.assertIn("X_USER_ACCESS_TOKEN", text)
        self.assertIn("/2/users/me", text)
        self.assertIn("reverse_chronological", text)
        self.assertNotIn("X_API_KEY", text)
        self.assertNotIn("X_API_SECRET", text)
        self.assertNotIn("X_ACCESS_TOKEN_SECRET", text)

    def test_item_filter_contract_is_source_scoped(self) -> None:
        text = (self.repo_root / "agents" / "item-filter.md").read_text(encoding="utf-8")
        self.assertIn("source_id", text)
        self.assertIn("same source", text)
        self.assertIn("All `item_paths` in one invocation must belong to the same source", text)

    def test_skill_documents_source_scoped_filter_batches(self) -> None:
        text = (self.skill_dir / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("build_filter_batches.py", text)
        self.assertIn("<= 30", text)
        self.assertIn("chunks of 10", text)
        self.assertIn("source_id, source_title, item_paths", text)


class GithubUserHandleContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.fixtures = self.repo_root / "tests" / "fixtures"
        self.window = TimeWindow(
            start=datetime(2026, 3, 17, 16, 0, tzinfo=UTC),
            end=datetime(2026, 3, 18, 16, 0, tzinfo=UTC),
        )
        self.fetched_at = datetime(2026, 3, 18, 8, 0, tzinfo=UTC)

    def test_github_user_handle_only_uses_default_api_endpoint(self) -> None:
        source = SourceSpec(
            id="fixture-github-user",
            title="Fixture GitHub User",
            kind="github_user",
            enabled=True,
            fetch={"handle": "sample-researcher"},
            notes="fixture",
        )
        client = StubHttpClient(self.fixtures / "github_user_events.json")
        raw_records = fetch_raw_records(source, client=client, fetched_at=self.fetched_at)
        items = normalize_raw_records(source, raw_records, window=self.window)

        self.assertEqual(client.seen_url, "https://api.github.com/users/sample-researcher/events/public")
        self.assertEqual(len(items), 2)

    def test_http_client_sets_github_api_headers(self) -> None:
        client = HttpClient(github_token="secret-token")
        headers = client._headers_for("https://api.github.com/user")
        self.assertEqual(headers["Accept"], "application/vnd.github+json")
        self.assertEqual(headers["X-GitHub-Api-Version"], "2022-11-28")
        self.assertEqual(headers["Authorization"], "Bearer secret-token")


class StubHttpClient(HttpClient):
    def __init__(self, fixture_path: Path) -> None:
        super().__init__()
        self.fixture_path = fixture_path
        self.seen_url: str | None = None

    def get_json(self, url: str):  # type: ignore[override]
        self.seen_url = url
        return json.loads(self.fixture_path.read_text(encoding="utf-8"))
