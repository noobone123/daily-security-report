from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from skill_lib import (
    CollectedItem,
    ValidationError,
    build_day_window,
    build_time_window,
    dedupe_items,
    find_last_window_end,
    load_seen_urls,
    run_collection,
    sort_items,
    write_json,
)


class CollectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.fixtures = self.repo_root / "tests" / "fixtures"

    def test_collect_materials_writes_index_items_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            self._write_fixture_workspace(workspace)
            manifest = run_collection(workspace, date_slug="2026-03-18", timezone="Asia/Shanghai")
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

    def test_collect_materials_records_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            self._write_fixture_workspace(workspace, broken_rss=True)
            manifest = run_collection(workspace, date_slug="2026-03-18", timezone="Asia/Shanghai")
            index_text = (workspace / "data" / "runs" / "2026-03-18" / "index.md").read_text(encoding="utf-8")
            self.assertEqual(manifest["failure_count"], 1)
            self.assertIn("fixture-rss", index_text)

    def test_collect_materials_writes_empty_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            self._write_fixture_workspace(workspace)
            manifest = run_collection(workspace, date_slug="2026-03-20", timezone="Asia/Shanghai", days=1)
            index_text = (workspace / "data" / "runs" / "2026-03-20" / "index.md").read_text(encoding="utf-8")
            self.assertEqual(manifest["item_count"], 0)
            self.assertIn("No items collected for this day.", index_text)

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
    ) -> CollectedItem:
        return CollectedItem(
            item_id=item_id,
            source_id="fixture-web",
            kind="web",
            external_id=item_id,
            canonical_url=canonical_url,
            title=item_id,
            author=None,
            published_at=published_at,
            fetched_at=published_at,
            excerpt=excerpt,
            content_text=content_text,
            language="en",
        )


class ManifestFieldsTest(unittest.TestCase):
    """Tests for manifest fields added in Feature 2 (warnings, collected_urls)."""

    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.fixtures = self.repo_root / "tests" / "fixtures"

    def test_manifest_has_warnings_and_collected_urls(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            self._write_fixture_workspace(workspace)
            manifest = run_collection(workspace, date_slug="2026-03-18", timezone="Asia/Shanghai")
            self.assertIn("warnings", manifest)
            self.assertIsInstance(manifest["warnings"], list)
            self.assertIn("collected_urls", manifest)
            self.assertIsInstance(manifest["collected_urls"], list)
            self.assertIn("seen_urls", manifest)

    def test_github_token_warning_when_unset(self) -> None:
        import os
        saved = os.environ.pop("GITHUB_TOKEN", None)
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                workspace = Path(tmpdir)
                self._write_fixture_workspace(workspace)
                manifest = run_collection(workspace, date_slug="2026-03-18", timezone="Asia/Shanghai")
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
