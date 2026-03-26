from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
import shutil
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from build_filter_batches import build_batches_for_run
from skill_lib import (
    CollectedItem,
    FetchError,
    ValidationError,
    _item_dedupe_key,
    config_path_for_templates,
    build_day_window,
    build_time_window,
    dedupe_items,
    find_last_window_end,
    load_seen_item_keys,
    load_seen_urls,
    resolve_source,
    run_collection,
    sort_items,
    write_workspace_config,
    write_json,
)


class CollectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.fixtures = self.repo_root / "tests" / "fixtures"
        self.skill_src = self.repo_root / "skills" / "daily-security-digest"

    def test_collect_materials_writes_index_items_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            self._write_fixture_workspace(workspace)
            templates_dir = self._make_templates_dir(Path(tmpdir))
            write_workspace_config(config_path_for_templates(templates_dir), workspace)
            manifest = run_collection(templates_dir, date_slug="2026-03-18", timezone="Asia/Shanghai")
            run_dir = workspace / "data" / "runs" / "2026-03-18"
            index_path = run_dir / "index.md"
            manifest_path = run_dir / "manifest.json"
            self.assertGreater(manifest["item_count"], 0)
            self.assertIn("agent_sources", manifest)
            self.assertEqual(len(manifest["agent_sources"]), 2)  # fixture-web + fixture-conference
            self.assertTrue(index_path.exists())
            self.assertTrue(manifest_path.exists())
            self.assertFalse((run_dir / "report.md").exists())
            index_text = index_path.read_text(encoding="utf-8")
            self.assertIn("## Items", index_text)
            self.assertIn("Fixture GitHub User", index_text)
            manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest_payload["item_count"], manifest["item_count"])
            first_item = run_dir / manifest["item_files"][0]
            self.assertTrue(first_item.exists())
            item_text = first_item.read_text(encoding="utf-8")
            self.assertIn("## Summary", item_text)
            self.assertIn("## Content", item_text)
            self.assertEqual(manifest["workspace"], str(workspace.resolve()))
            self.assertEqual(manifest["workspace_config_path"], str(config_path_for_templates(templates_dir).resolve()))
            self.assertEqual(manifest["planning_dir"], str((workspace / "planning").resolve()))
            self.assertEqual(manifest["runs_dir"], str((workspace / "data" / "runs").resolve()))

    def test_collect_materials_records_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            self._write_fixture_workspace(workspace, broken_rss=True)
            templates_dir = self._make_templates_dir(Path(tmpdir))
            write_workspace_config(config_path_for_templates(templates_dir), workspace)
            manifest = run_collection(templates_dir, date_slug="2026-03-18", timezone="Asia/Shanghai")
            index_text = (workspace / "data" / "runs" / "2026-03-18" / "index.md").read_text(encoding="utf-8")
            self.assertEqual(manifest["failure_count"], 1)
            self.assertIn("fixture-rss", index_text)

    def test_collect_materials_writes_empty_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            self._write_fixture_workspace(workspace)
            templates_dir = self._make_templates_dir(Path(tmpdir))
            write_workspace_config(config_path_for_templates(templates_dir), workspace)
            manifest = run_collection(templates_dir, date_slug="2026-03-20", timezone="Asia/Shanghai", days=1)
            index_text = (workspace / "data" / "runs" / "2026-03-20" / "index.md").read_text(encoding="utf-8")
            self.assertEqual(manifest["item_count"], 0)
            self.assertIn("No items collected for this day.", index_text)

    def test_run_collection_uses_workspace_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir) / "repo"
            self._write_fixture_workspace(repo_root)
            templates_dir = self._make_templates_dir(Path(tmpdir))
            write_workspace_config(config_path_for_templates(templates_dir), repo_root)

            manifest = run_collection(templates_dir, date_slug="2026-03-18", timezone="Asia/Shanghai")
            self.assertEqual(manifest["workspace"], str(repo_root.resolve()))
            self.assertEqual(manifest["workspace_config_path"], str(config_path_for_templates(templates_dir).resolve()))
            self.assertEqual(manifest["runs_dir"], str((repo_root / "data" / "runs").resolve()))
            self.assertTrue((repo_root / "data" / "runs" / "2026-03-18" / "manifest.json").exists())

    def test_run_collection_rejects_missing_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            templates_dir = self._make_templates_dir(Path(tmpdir))
            with self.assertRaisesRegex(ValidationError, "Missing workspace config"):
                run_collection(templates_dir, date_slug="2026-03-18", timezone="Asia/Shanghai")

    def test_collect_materials_cli_reports_workspace_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir()
            self._write_fixture_workspace(workspace)
            skill_dir = Path(tmpdir) / "skills" / "daily-security-digest"
            shutil.copytree(self.skill_src / "scripts", skill_dir / "scripts")
            shutil.copytree(self.skill_src / "templates", skill_dir / "templates")
            write_workspace_config(skill_dir / "config.toml", workspace)
            script_path = skill_dir / "scripts" / "collect_materials.py"

            result = subprocess.run(
                [
                    "python3",
                    str(script_path),
                    "--date",
                    "2026-03-18",
                    "--timezone",
                    "Asia/Shanghai",
                ],
                capture_output=True,
                text=True,
                check=False,
                cwd=self.repo_root,
            )
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["workspace"], str(workspace.resolve()))
            self.assertEqual(payload["workspace_config_path"], str((skill_dir / "config.toml").resolve()))
            self.assertEqual(payload["planning_dir"], str((workspace / "planning").resolve()))
            self.assertEqual(payload["runs_dir"], str((workspace / "data" / "runs").resolve()))
            self.assertEqual(payload["manifest"]["workspace"], str(workspace.resolve()))

    def test_dedupe_prefers_richer_item_and_sorting_is_newest_first(self) -> None:
        older_rich = self._make_item(
            item_id="older-rich",
            published_at=datetime(2026, 3, 18, 1, 0, tzinfo=UTC),
            excerpt="rich summary",
            content_text="A" * 200,
            canonical_url="https://example.com/shared",
        )
        newer_thin = self._make_item(
            item_id="newer-thin",
            published_at=datetime(2026, 3, 18, 2, 0, tzinfo=UTC),
            excerpt="thin",
            content_text="B" * 20,
            canonical_url="https://example.com/shared",
        )
        kept = dedupe_items([newer_thin, older_rich])
        self.assertEqual([item.item_id for item in kept], ["older-rich"])

        older = self._make_item(
            item_id="older",
            published_at=datetime(2026, 3, 18, 1, 0, tzinfo=UTC),
            excerpt="older",
            content_text="A" * 40,
            canonical_url="https://example.com/older",
        )
        newer = self._make_item(
            item_id="newer",
            published_at=datetime(2026, 3, 18, 3, 0, tzinfo=UTC),
            excerpt="newer",
            content_text="B" * 40,
            canonical_url="https://example.com/newer",
        )
        ordered = sort_items([older, newer])
        self.assertEqual([item.item_id for item in ordered], ["newer", "older"])

    def test_watch_events_do_not_dedupe_by_repository_url(self) -> None:
        first_watch = self._make_item(
            item_id="watch-1",
            published_at=datetime(2026, 3, 18, 1, 0, tzinfo=UTC),
            excerpt="first watch",
            content_text="star one",
            canonical_url="https://github.com/example/shared-repo",
            kind="watchevent",
            external_id="evt-1",
        )
        second_watch = self._make_item(
            item_id="watch-2",
            published_at=datetime(2026, 3, 18, 2, 0, tzinfo=UTC),
            excerpt="second watch",
            content_text="star two",
            canonical_url="https://github.com/example/shared-repo",
            kind="watchevent",
            external_id="evt-2",
        )
        kept = sort_items(dedupe_items([first_watch, second_watch]))
        self.assertEqual([item.item_id for item in kept], ["watch-2", "watch-1"])
        self.assertEqual(_item_dedupe_key(first_watch), "event:evt-1")

    def _write_fixture_workspace(self, workspace: Path, *, broken_rss: bool = False) -> None:
        (workspace / "planning").mkdir(parents=True, exist_ok=True)
        (workspace / "data" / "runs").mkdir(parents=True, exist_ok=True)
        (workspace / "planning" / "report-style.md").write_text(
            "# Report Style\n\n## Audience\n\nDaily operator.\n\n## Language\n\nChinese commentary.\n\n## Output Format\n\nMarkdown.\n\n## Extra Instructions\n\nPrefer high signal.\n",
            encoding="utf-8",
        )
        rss_url = "file:///definitely-missing-feed.xml" if broken_rss else self._uri("rss.xml")
        sources_toml = (
            '[[sources]]\n'
            'id = "fixture-github-user"\n'
            'title = "Fixture GitHub User"\n'
            'kind = "github_user"\n'
            'enabled = true\n'
            'notes = "Fixture source."\n'
            'fetch.handle = "sample-researcher"\n'
            'fetch.events_url = "' + self._uri("github_user_events.json") + '"\n'
            '\n'
            '[[sources]]\n'
            'id = "fixture-rss"\n'
            'title = "Fixture RSS"\n'
            'kind = "rss"\n'
            'enabled = true\n'
            'notes = "Fixture source."\n'
            'fetch.url = "' + rss_url + '"\n'
            '\n'
            '[[sources]]\n'
            'id = "fixture-web"\n'
            'title = "Fixture Web"\n'
            'kind = "web"\n'
            'enabled = true\n'
            'notes = "Fixture source."\n'
            'fetch.url = "https://example.com/research/"\n'
            '\n'
            '[[sources]]\n'
            'id = "fixture-conference"\n'
            'title = "Fixture Conference"\n'
            'kind = "web"\n'
            'enabled = true\n'
            'notes = "Fixture source."\n'
            'fetch.url = "https://example.com/conference/program/"\n'
        )
        (workspace / "planning" / "sources.toml").write_text(sources_toml, encoding="utf-8")

    def _make_templates_dir(self, root: Path) -> Path:
        templates_dir = root / "skill" / "templates"
        templates_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.skill_src / "templates", templates_dir)
        return templates_dir

    def _uri(self, name: str) -> str:
        return (self.fixtures / name).resolve().as_uri()

    def _make_item(
        self,
        *,
        item_id: str,
        published_at: datetime,
        excerpt: str,
        content_text: str,
        canonical_url: str,
        kind: str = "web",
        external_id: str | None = None,
    ) -> CollectedItem:
        return CollectedItem(
            item_id=item_id,
            source_id="fixture-web",
            kind=kind,
            external_id=external_id or item_id,
            canonical_url=canonical_url,
            title=item_id,
            author=None,
            published_at=published_at,
            fetched_at=published_at,
            excerpt=excerpt,
            content_text=content_text,
            language="en",
        )


class ResolveSourceTest(unittest.TestCase):
    def test_resolve_source_detects_github_feed(self) -> None:
        resolved = resolve_source("https://github.com/", user_label="")
        self.assertEqual(resolved["kind"], "github_feed")
        self.assertEqual(resolved["fetch"]["handle"], "@authenticated")
        self.assertIn("GITHUB_TOKEN", resolved["notes"])

    def test_resolve_source_rejects_x_urls(self) -> None:
        with self.assertRaisesRegex(ValueError, "X/Twitter sources are no longer supported"):
            resolve_source("https://x.com/home", user_label="")
        with self.assertRaisesRegex(ValueError, "X/Twitter sources are no longer supported"):
            resolve_source("https://twitter.com/home", user_label="")

    def test_resolve_source_detects_github_username(self) -> None:
        resolved = resolve_source("sample-researcher", user_label="")
        self.assertEqual(resolved["kind"], "github_user")
        self.assertEqual(resolved["fetch"]["handle"], "sample-researcher")
        self.assertEqual(resolved["id"], "sample-researcher")

    def test_resolve_source_detects_feed_url(self) -> None:
        resolved = resolve_source("https://example.com/feed.xml", user_label="")
        self.assertEqual(resolved["kind"], "rss")
        self.assertEqual(resolved["fetch"]["url"], "https://example.com/feed.xml")

    def test_resolve_source_discovers_rss_from_html(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            feed_uri = (root / "feed.xml").resolve().as_uri()
            page = root / "index.html"
            page.write_text(
                (
                    "<html><head>"
                    "<title>Example Security</title>"
                    f'<link rel="alternate" type="application/rss+xml" href="{feed_uri}">'
                    "</head><body>hello</body></html>"
                ),
                encoding="utf-8",
            )
            resolved = resolve_source(page.resolve().as_uri(), user_label="")
        self.assertEqual(resolved["kind"], "rss")
        self.assertEqual(resolved["fetch"]["url"], feed_uri)
        self.assertEqual(resolved["title"], "Example Security")

    def test_resolve_source_falls_back_to_web_when_fetch_fails(self) -> None:
        with mock.patch("skill_lib.HttpClient.get_text", side_effect=FetchError("boom")):
            resolved = resolve_source("https://example.com/security", user_label="")
        self.assertEqual(resolved["kind"], "web")
        self.assertEqual(resolved["fetch"]["url"], "https://example.com/security")
        self.assertIn("boom", resolved["notes"])

    def test_resolve_source_cli_returns_json(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        script_path = repo_root / "skills" / "daily-security-digest" / "scripts" / "resolve_source.py"
        result = subprocess.run(
            ["python3", str(script_path), "--input", "sample-researcher"],
            capture_output=True,
            text=True,
            check=False,
            cwd=repo_root,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"]["kind"], "github_user")


class WebCollectionCliTest(unittest.TestCase):
    def test_placeholder(self) -> None:
        self.assertTrue(True)


class ManifestFieldsTest(unittest.TestCase):
    """Tests for manifest fields added in Feature 2 (warnings, collected_urls)."""

    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.fixtures = self.repo_root / "tests" / "fixtures"
        self.skill_src = self.repo_root / "skills" / "daily-security-digest"

    def test_manifest_has_warnings_and_collected_urls(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            self._write_fixture_workspace(workspace)
            templates_dir = self._make_templates_dir(Path(tmpdir))
            write_workspace_config(config_path_for_templates(templates_dir), workspace)
            manifest = run_collection(templates_dir, date_slug="2026-03-18", timezone="Asia/Shanghai")
            self.assertIn("warnings", manifest)
            self.assertIsInstance(manifest["warnings"], list)
            self.assertIn("collected_urls", manifest)
            self.assertIsInstance(manifest["collected_urls"], list)
            self.assertIn("collected_item_keys", manifest)
            self.assertIsInstance(manifest["collected_item_keys"], list)
            self.assertIn("seen_urls", manifest)
            self.assertIn("seen_item_keys", manifest)

    def test_github_token_warning_when_unset(self) -> None:
        import os
        saved = os.environ.pop("GITHUB_TOKEN", None)
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                workspace = Path(tmpdir)
                self._write_fixture_workspace(workspace)
                templates_dir = self._make_templates_dir(Path(tmpdir))
                write_workspace_config(config_path_for_templates(templates_dir), workspace)
                manifest = run_collection(templates_dir, date_slug="2026-03-18", timezone="Asia/Shanghai")
                token_warnings = [w for w in manifest["warnings"] if "GITHUB_TOKEN" in w]
                self.assertEqual(len(token_warnings), 1)
        finally:
            if saved is not None:
                os.environ["GITHUB_TOKEN"] = saved

    def _write_fixture_workspace(self, workspace: Path) -> None:
        (workspace / "planning").mkdir(parents=True, exist_ok=True)
        (workspace / "planning" / "report-style.md").write_text(
            "# Report Style\n\n## Audience\n\nAnalyst.\n\n## Language\n\nEnglish.\n\n## Output Format\n\nMarkdown.\n\n## Extra Instructions\n\nKeep it short.\n",
            encoding="utf-8",
        )
        sources_toml = (
            '[[sources]]\n'
            'id = "fixture-github-user"\n'
            'title = "Fixture GitHub User"\n'
            'kind = "github_user"\n'
            'enabled = true\n'
            'fetch.handle = "sample-researcher"\n'
            'fetch.events_url = "' + (self.fixtures / "github_user_events.json").resolve().as_uri() + '"\n'
        )
        (workspace / "planning" / "sources.toml").write_text(sources_toml, encoding="utf-8")

    def _make_templates_dir(self, root: Path) -> Path:
        templates_dir = root / "skill" / "templates"
        templates_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.skill_src / "templates", templates_dir)
        return templates_dir


class GithubFeedCollectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.skill_src = self.repo_root / "skills" / "daily-security-digest"

    def test_github_feed_without_token_warns_and_records_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            self._write_github_feed_workspace(workspace, handle="@authenticated")
            templates_dir = self._make_templates_dir(Path(tmpdir))
            write_workspace_config(config_path_for_templates(templates_dir), workspace)
            with mock.patch.dict("os.environ", {"GITHUB_TOKEN": ""}, clear=False):
                manifest = run_collection(templates_dir, date_slug="2026-03-18", timezone="Asia/Shanghai")
        token_warnings = [warning for warning in manifest["warnings"] if "github_feed" in warning and "GITHUB_TOKEN" in warning]
        self.assertEqual(len(token_warnings), 1)
        self.assertEqual(manifest["failure_count"], 1)
        self.assertEqual(manifest["item_count"], 0)
        self.assertIn("github_feed requires GITHUB_TOKEN", manifest["failures"][0]["error"])

    def test_github_feed_handle_mismatch_records_failure(self) -> None:
        seen_urls: list[str] = []

        def fake_get_json(_client, url: str):
            seen_urls.append(url)
            if url == "https://api.github.com/user":
                return {"login": "actual-user"}
            raise AssertionError(f"Unexpected URL: {url}")

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            self._write_github_feed_workspace(workspace, handle="someone-else")
            templates_dir = self._make_templates_dir(Path(tmpdir))
            write_workspace_config(config_path_for_templates(templates_dir), workspace)
            with mock.patch.dict("os.environ", {"GITHUB_TOKEN": "test-token"}, clear=False):
                with mock.patch("skill_lib.HttpClient.get_json", new=fake_get_json):
                    manifest = run_collection(templates_dir, date_slug="2026-03-18", timezone="Asia/Shanghai")
        self.assertEqual(seen_urls, ["https://api.github.com/user"])
        self.assertEqual(manifest["failure_count"], 1)
        self.assertFalse(any("github_feed" in warning and "GITHUB_TOKEN" in warning for warning in manifest["warnings"]))
        self.assertIn("does not match authenticated GitHub user 'actual-user'", manifest["failures"][0]["error"])
        self.assertIn("Use github_user for public profile events", manifest["failures"][0]["error"])

    def _write_github_feed_workspace(self, workspace: Path, *, handle: str) -> None:
        (workspace / "planning").mkdir(parents=True, exist_ok=True)
        (workspace / "planning" / "report-style.md").write_text(
            "# Report Style\n\n## Audience\n\nAnalyst.\n\n## Language\n\nEnglish.\n\n## Output Format\n\nMarkdown.\n\n## Extra Instructions\n\nKeep it short.\n",
            encoding="utf-8",
        )
        sources_toml = (
            '[[sources]]\n'
            'id = "fixture-github-feed"\n'
            'title = "Fixture GitHub Feed"\n'
            'kind = "github_feed"\n'
            'enabled = true\n'
            'fetch.handle = "' + handle + '"\n'
        )
        (workspace / "planning" / "sources.toml").write_text(sources_toml, encoding="utf-8")

    def _make_templates_dir(self, root: Path) -> Path:
        templates_dir = root / "skill" / "templates"
        templates_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.skill_src / "templates", templates_dir)
        return templates_dir


class FilterBatchPlannerTest(unittest.TestCase):
    def test_build_filter_batches_keeps_sources_separate_when_small(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            item_dir = workspace / "data" / "runs" / "2026-03-18" / "items"
            item_dir.mkdir(parents=True)
            self._write_item(
                item_dir / "a1.md",
                title="A1",
                source_title="Source A",
                source_id="source-a",
                published_at="2026-03-18T03:00:00+00:00",
                url="https://example.com/a1",
            )
            self._write_item(
                item_dir / "a2.md",
                title="A2",
                source_title="Source A",
                source_id="source-a",
                published_at="2026-03-18T01:00:00+00:00",
                url="https://example.com/a2",
            )
            self._write_item(
                item_dir / "b1.md",
                title="B1",
                source_title="Source B",
                source_id="source-b",
                published_at="2026-03-18T02:00:00+00:00",
                url="https://example.com/b1",
            )
            payload = build_batches_for_run(workspace=workspace, run_date="2026-03-18")
        self.assertEqual(payload["batch_count"], 2)
        batches_by_source = {batch["source_id"]: batch for batch in payload["batches"]}
        self.assertEqual(
            [Path(path).name for path in batches_by_source["source-a"]["item_paths"]],
            ["a1.md", "a2.md"],
        )
        self.assertEqual(
            [Path(path).name for path in batches_by_source["source-b"]["item_paths"]],
            ["b1.md"],
        )

    def test_build_filter_batches_splits_large_source_into_chunks_of_ten(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            item_dir = workspace / "data" / "runs" / "2026-03-18" / "items"
            item_dir.mkdir(parents=True)
            for index in range(31):
                self._write_item(
                    item_dir / f"x{index:02d}.md",
                    title=f"Item {index}",
                    source_title="Source X",
                    source_id="source-x",
                    published_at=f"2026-03-18T{index % 24:02d}:00:00+00:00",
                    url=f"https://example.com/x{index}",
                )
            payload = build_batches_for_run(workspace=workspace, run_date="2026-03-18")
        self.assertEqual(payload["batch_count"], 4)
        sizes = [len(batch["item_paths"]) for batch in payload["batches"]]
        self.assertEqual(sizes, [10, 10, 10, 1])
        self.assertTrue(all(batch["source_id"] == "source-x" for batch in payload["batches"]))

    def test_build_filter_batches_sorts_within_source_by_published_at_desc(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            item_dir = workspace / "data" / "runs" / "2026-03-18" / "items"
            item_dir.mkdir(parents=True)
            self._write_item(
                item_dir / "late.md",
                title="Late",
                source_title="Source A",
                source_id="source-a",
                published_at="2026-03-18T03:00:00+00:00",
                url="https://example.com/late",
            )
            self._write_item(
                item_dir / "early.md",
                title="Early",
                source_title="Source A",
                source_id="source-a",
                published_at="2026-03-18T01:00:00+00:00",
                url="https://example.com/early",
            )
            self._write_item(
                item_dir / "mid.md",
                title="Mid",
                source_title="Source A",
                source_id="source-a",
                published_at="2026-03-18T02:00:00+00:00",
                url="https://example.com/mid",
            )
            payload = build_batches_for_run(workspace=workspace, run_date="2026-03-18")
        self.assertEqual(
            [Path(path).name for path in payload["batches"][0]["item_paths"]],
            ["late.md", "mid.md", "early.md"],
        )

    def _write_item(
        self,
        path: Path,
        *,
        title: str,
        source_title: str,
        source_id: str,
        published_at: str,
        url: str,
    ) -> None:
        path.write_text(
            "\n".join(
                [
                    f"# {title}",
                    "",
                    "## Source",
                    "",
                    f"{source_title} (`{source_id}`)",
                    "",
                    "## Published At",
                    "",
                    published_at,
                    "",
                    "## URL",
                    "",
                    url,
                    "",
                    "## Summary",
                    "",
                    "summary",
                    "",
                    "## Content",
                    "",
                    "content",
                    "",
                ]
            ),
            encoding="utf-8",
        )


class SeenUrlsTest(unittest.TestCase):
    """Tests for cross-run dedup (Feature 3)."""

    def test_load_seen_urls_returns_empty_for_no_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertEqual(load_seen_urls(Path(tmpdir)), set())

    def test_load_seen_urls_reads_previous_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_dir = root / "data" / "runs" / "2026-03-17"
            run_dir.mkdir(parents=True)
            write_json(run_dir / "manifest.json", {"collected_urls": ["https://a.com", "https://b.com"]})
            seen = load_seen_urls(root)
            self.assertEqual(seen, {"https://a.com", "https://b.com"})

    def test_load_seen_item_keys_reads_new_manifest_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_dir = root / "data" / "runs" / "2026-03-17"
            run_dir.mkdir(parents=True)
            write_json(run_dir / "manifest.json", {"collected_item_keys": ["event:1", "https://b.com"]})
            seen = load_seen_item_keys(root)
            self.assertEqual(seen, {"event:1", "https://b.com"})

    def test_load_seen_item_keys_falls_back_to_urls_for_old_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_dir = root / "data" / "runs" / "2026-03-17"
            run_dir.mkdir(parents=True)
            write_json(run_dir / "manifest.json", {"collected_urls": ["https://a.com"]})
            seen = load_seen_item_keys(root)
            self.assertEqual(seen, {"https://a.com"})

    def test_load_seen_urls_excludes_current_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for slug, urls in [("2026-03-17", ["https://a.com"]), ("2026-03-18", ["https://b.com"])]:
                run_dir = root / "data" / "runs" / slug
                run_dir.mkdir(parents=True)
                write_json(run_dir / "manifest.json", {"collected_urls": urls})
            seen = load_seen_urls(root, exclude_date="2026-03-18")
            self.assertIn("https://a.com", seen)
            self.assertNotIn("https://b.com", seen)


class TimeWindowTest(unittest.TestCase):
    """Tests for time window construction (Feature 4)."""

    def test_build_time_window_single_day_matches_build_day_window(self) -> None:
        w1 = build_day_window("2026-03-18", "Asia/Shanghai")
        w2 = build_time_window("2026-03-18", "Asia/Shanghai", days=1)
        self.assertEqual(w1.start, w2.start)
        self.assertEqual(w1.end, w2.end)

    def test_build_time_window_multi_day(self) -> None:
        w = build_time_window("2026-03-18", "Asia/Shanghai", days=3)
        w_end = build_day_window("2026-03-18", "Asia/Shanghai")
        self.assertEqual(w.end, w_end.end)
        w_start_ref = build_day_window("2026-03-16", "Asia/Shanghai")
        self.assertEqual(w.start, w_start_ref.start)

    def test_build_time_window_rejects_zero_days(self) -> None:
        with self.assertRaises(ValidationError):
            build_time_window("2026-03-18", "Asia/Shanghai", days=0)

    def test_find_last_window_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for slug, end in [("2026-03-16", "2026-03-17T00:00:00+00:00"), ("2026-03-17", "2026-03-18T00:00:00+00:00")]:
                run_dir = root / "data" / "runs" / slug
                run_dir.mkdir(parents=True)
                write_json(run_dir / "manifest.json", {"window_end": end})
            result = find_last_window_end(root)
            self.assertIsNotNone(result)
            self.assertEqual(result.isoformat(), "2026-03-18T00:00:00+00:00")

    def test_find_last_window_end_returns_none_for_no_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertIsNone(find_last_window_end(Path(tmpdir)))


if __name__ == "__main__":
    unittest.main()
