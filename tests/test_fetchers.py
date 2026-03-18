from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path

from skill_lib import HttpClient, SourceSpec, TimeWindow, fetch_raw_records, normalize_raw_records


class FetcherTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixtures = Path(__file__).parent / "fixtures"
        self.client = HttpClient()
        self.window = TimeWindow(
            start=datetime(2026, 3, 17, 16, 0, tzinfo=UTC),
            end=datetime(2026, 3, 18, 16, 0, tzinfo=UTC),
        )
        self.fetched_at = datetime(2026, 3, 18, 8, 0, tzinfo=UTC)

    def test_github_user_filters_to_supported_event_types(self) -> None:
        source = SourceSpec(
            id="fixture-github-user",
            title="Fixture GitHub User",
            kind="github_user",
            enabled=True,
            fetch={"handle": "sample-researcher", "events_url": self._file_url("github_user_events.json")},
            notes="fixture",
        )
        raw_records = fetch_raw_records(source, client=self.client, fetched_at=self.fetched_at)
        items = normalize_raw_records(source, raw_records, window=self.window)
        self.assertEqual(len(raw_records), 3)
        self.assertEqual(len(items), 2)
        self.assertEqual({item.kind for item in items}, {"releaseevent", "pushevent"})

    def test_rss_normalization(self) -> None:
        rss_source = SourceSpec(
            id="fixture-rss",
            title="Fixture RSS",
            kind="rss",
            enabled=True,
            fetch={"url": self._file_url("rss.xml")},
            notes="fixture",
        )
        rss_items = normalize_raw_records(
            rss_source,
            fetch_raw_records(rss_source, client=self.client, fetched_at=self.fetched_at),
            window=self.window,
        )
        self.assertEqual(len(rss_items), 1)
        self.assertEqual(rss_items[0].canonical_url, "https://example.com/blog/cloud-detection-rule-update")

    def _file_url(self, name: str) -> str:
        return (self.fixtures / name).resolve().as_uri()


if __name__ == "__main__":
    unittest.main()
