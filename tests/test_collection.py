from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from skill_lib import CollectedItem, dedupe_items, run_collection, sort_items


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
            manifest = run_collection(workspace, date_slug="2026-03-20", timezone="Asia/Shanghai")
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


if __name__ == "__main__":
    unittest.main()
