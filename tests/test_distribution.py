from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from skill_lib import HttpClient, SourceSpec, TimeWindow, bootstrap_planning, fetch_raw_records, normalize_raw_records


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

    def test_install_script_project_mode_creates_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "project"
            target.mkdir()
            result = subprocess.run(
                ["bash", str(self.repo_root / "scripts" / "install.sh"), "--mode", "project", "--target", str(target)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

            skill_link = target / ".claude" / "skills" / "daily-security-digest"
            self.assertTrue(skill_link.is_symlink())
            self.assertEqual(skill_link.resolve(), (self.repo_root / "skills" / "daily-security-digest").resolve())

            for agent_name in ("source-resolver", "web-source-collector", "item-filter", "report-writer"):
                agent_link = target / ".claude" / "agents" / f"{agent_name}.md"
                self.assertTrue(agent_link.is_symlink())
                self.assertEqual(agent_link.resolve(), (self.repo_root / "agents" / f"{agent_name}.md").resolve())

    def test_bootstrap_planning_uses_skill_templates_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = bootstrap_planning(Path(tmpdir), self.templates_dir)
            created = {Path(path).name for path in payload["created"]}
            self.assertEqual(created, {"sources.toml", "topics.md", "report-style.md"})

    def test_repo_has_single_canonical_agent_set(self) -> None:
        self.assertFalse((self.repo_root / ".claude" / "agents" / "source-resolver.md").exists())
        self.assertFalse((self.repo_root / ".claude" / "agents" / "web-source-collector.md").exists())
        self.assertFalse((self.repo_root / ".claude" / "agents" / "item-filter.md").exists())
        self.assertFalse((self.repo_root / ".claude" / "agents" / "report-writer.md").exists())

    def test_source_resolver_contract_documents_handle(self) -> None:
        text = (self.repo_root / "agents" / "source-resolver.md").read_text(encoding="utf-8")
        self.assertIn("fetch.handle", text)
        self.assertNotIn("fetch.username", text)


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


class StubHttpClient(HttpClient):
    def __init__(self, fixture_path: Path) -> None:
        super().__init__()
        self.fixture_path = fixture_path
        self.seen_url: str | None = None

    def get_json(self, url: str):  # type: ignore[override]
        self.seen_url = url
        return json.loads(self.fixture_path.read_text(encoding="utf-8"))
