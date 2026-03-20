from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from skill_lib import FetchError, HttpClient, SourceSpec, TimeWindow, fetch_raw_records, normalize_raw_records


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

    def test_github_feed_requires_token(self) -> None:
        source = SourceSpec(
            id="fixture-github-feed",
            title="Fixture GitHub Feed",
            kind="github_feed",
            enabled=True,
            fetch={"handle": "@authenticated"},
            notes="fixture",
        )
        with self.assertRaisesRegex(FetchError, "github_feed requires GITHUB_TOKEN"):
            fetch_raw_records(source, client=self.client, fetched_at=self.fetched_at)

    def test_github_feed_resolves_authenticated_user_and_keeps_feed_events(self) -> None:
        source = SourceSpec(
            id="fixture-github-feed",
            title="Fixture GitHub Feed",
            kind="github_feed",
            enabled=True,
            fetch={"handle": "@authenticated"},
            notes="fixture",
        )
        client = UrlMapHttpClient(
            {
                "https://api.github.com/user": self._json_fixture("github_authenticated_user.json"),
                "https://api.github.com/users/sample-operator/received_events?per_page=100": self._json_fixture("github_feed_events.json"),
            },
            github_token="test-token",
        )
        raw_records = fetch_raw_records(source, client=client, fetched_at=self.fetched_at)
        items = normalize_raw_records(source, raw_records, window=self.window)
        self.assertEqual(
            client.seen_urls,
            [
                "https://api.github.com/user",
                "https://api.github.com/users/sample-operator/received_events?per_page=100",
            ],
        )
        self.assertEqual(len(raw_records), 3)
        self.assertEqual(len(items), 2)
        self.assertEqual({item.kind for item in items}, {"issuecommentevent", "issuesevent"})

    def test_x_home_requires_credentials(self) -> None:
        source = SourceSpec(
            id="fixture-x-home",
            title="Fixture X Home",
            kind="x_home",
            enabled=True,
            fetch={},
            notes="fixture",
        )
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(FetchError, "x_home requires X_USER_ACCESS_TOKEN"):
                fetch_raw_records(source, client=self.client, fetched_at=self.fetched_at)

    def test_x_home_normalization_covers_post_reply_quote_and_repost(self) -> None:
        source = SourceSpec(
            id="fixture-x-home",
            title="Fixture X Home",
            kind="x_home",
            enabled=True,
            fetch={},
            notes="fixture",
        )
        client = RequestAwareHttpClient(
            {
                "https://api.x.com/2/users/me?user.fields=id,username,name": self._json_fixture("x_me.json"),
                "https://api.x.com/2/users/42/timelines/reverse_chronological?max_results=100&expansions=author_id%2Creferenced_tweets.id%2Creferenced_tweets.id.author_id%2Cin_reply_to_user_id&tweet.fields=author_id%2Ccreated_at%2Cconversation_id%2Cin_reply_to_user_id%2Creferenced_tweets%2Ctext&user.fields=id%2Cname%2Cusername": self._json_fixture("x_home_timeline.json"),
            }
        )
        env = {
            "X_USER_ACCESS_TOKEN": "user-access-token",
        }
        with mock.patch.dict("os.environ", env, clear=True):
            raw_records = fetch_raw_records(source, client=client, fetched_at=self.fetched_at)
            items = normalize_raw_records(source, raw_records, window=self.window)
        self.assertEqual(len(raw_records), 4)
        self.assertEqual({item.kind for item in items}, {"x-post", "x-reply", "x-quote", "x-repost"})
        self.assertTrue(all(request["headers"].get("Authorization", "").startswith("Bearer ") for request in client.requests))
        self.assertIn("/2/users/me", client.requests[0]["url"])
        self.assertIn("/2/users/42/timelines/reverse_chronological", client.requests[1]["url"])
        self.assertIn("max_results=100", client.requests[1]["url"])
        canonical_urls = {item.kind: item.canonical_url for item in items}
        self.assertEqual(canonical_urls["x-post"], "https://x.com/alice_sec/status/100")
        self.assertEqual(canonical_urls["x-repost"], "https://x.com/alice_sec/status/103")

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

    def _json_fixture(self, name: str):
        return json.loads((self.fixtures / name).read_text(encoding="utf-8"))


class UrlMapHttpClient(HttpClient):
    def __init__(self, payloads: dict[str, object], *, github_token: str | None = None) -> None:
        super().__init__(github_token=github_token)
        self.payloads = payloads
        self.seen_urls: list[str] = []

    def get_json(self, url: str):  # type: ignore[override]
        self.seen_urls.append(url)
        if url not in self.payloads:
            raise AssertionError(f"Unexpected URL: {url}")
        return self.payloads[url]


class RequestAwareHttpClient(HttpClient):
    def __init__(self, payloads: dict[str, object]) -> None:
        super().__init__()
        self.payloads = payloads
        self.requests: list[dict[str, object]] = []

    def request_json(self, url: str, *, headers: dict[str, str] | None = None):  # type: ignore[override]
        self.requests.append({"url": url, "headers": headers or {}})
        if url not in self.payloads:
            raise AssertionError(f"Unexpected URL: {url}")
        return self.payloads[url]


if __name__ == "__main__":
    unittest.main()
