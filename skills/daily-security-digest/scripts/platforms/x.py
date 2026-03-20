from __future__ import annotations

import os
from typing import Any

X_API_BASE = "https://api.x.com"
X_USER_ACCESS_TOKEN_ENV = "X_USER_ACCESS_TOKEN"


def _core():
    import core

    return core


def validate_fetch(path, kind: str, fetch: dict[str, str]) -> None:
    _ = path, kind, fetch


def missing_credentials_warning() -> str | None:
    if _load_access_token():
        return None
    return f"X user access token not set for x_home sources. Set {X_USER_ACCESS_TOKEN_ENV}."


def fetch_raw_records(source, *, client, fetched_at):
    c = _core()
    access_token = _load_access_token()
    if not access_token:
        raise c.FetchError(f"{source.id}: x_home requires {X_USER_ACCESS_TOKEN_ENV}")
    max_results = min(c._fetch_int(source.fetch, "max_results", 100), 100)

    me_url = source.fetch.get("me_url") or _build_me_url()
    me_payload = client.request_json(me_url, headers=_bearer_headers(access_token))
    if not isinstance(me_payload, dict):
        raise c.FetchError(f"{source.id}: expected JSON object from {me_url}")
    me_user = me_payload.get("data", me_payload)
    if not isinstance(me_user, dict):
        raise c.FetchError(f"{source.id}: expected user object from {me_url}")
    user_id = str(me_user.get("id", "")).strip()
    if not user_id:
        raise c.FetchError(f"{source.id}: authenticated X user response missing 'id'")

    timeline_url = source.fetch.get("timeline_url") or _build_timeline_url(user_id, max_results=max_results)
    payload = client.request_json(timeline_url, headers=_bearer_headers(access_token))
    if not isinstance(payload, dict):
        raise c.FetchError(f"{source.id}: expected JSON object from {timeline_url}")

    data = payload.get("data")
    if data in (None, []):
        return []
    if not isinstance(data, list):
        raise c.FetchError(f"{source.id}: expected JSON list in timeline response from {timeline_url}")

    includes = payload.get("includes", {}) if isinstance(payload.get("includes"), dict) else {}
    users = includes.get("users", []) if isinstance(includes.get("users"), list) else []
    tweets = includes.get("tweets", []) if isinstance(includes.get("tweets"), list) else []
    users_by_id = {
        str(user.get("id")): user
        for user in users
        if isinstance(user, dict) and user.get("id")
    }
    tweets_by_id = {
        str(tweet.get("id")): tweet
        for tweet in tweets
        if isinstance(tweet, dict) and tweet.get("id")
    }

    rows = []
    for tweet in data[:max_results]:
        if not isinstance(tweet, dict):
            continue
        tweet_id = str(tweet.get("id", len(rows)))
        rows.append(
            c.RawRecord(
                raw_id=c.stable_id(source.id, "x_home", tweet_id),
                source_id=source.id,
                fetched_at=fetched_at,
                source_url=timeline_url,
                payload={
                    "tweet": tweet,
                    "users_by_id": users_by_id,
                    "tweets_by_id": tweets_by_id,
                },
            )
        )
    return rows


def _load_access_token() -> str:
    return str(os.environ.get(X_USER_ACCESS_TOKEN_ENV, "")).strip()


def _bearer_headers(access_token: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
    }


def normalize_raw_records(source, raw_records, *, window):
    c = _core()
    items = []
    for raw in raw_records:
        item = _normalize_tweet(source, raw, window=window)
        if item is not None:
            items.append(item)
    return items


def _normalize_tweet(source, raw, *, window):
    c = _core()
    payload = raw.payload
    tweet = payload.get("tweet", {})
    if not isinstance(tweet, dict):
        return None
    published_at = c.parse_datetime(tweet.get("created_at")) or raw.fetched_at
    if not c._in_window(published_at, window):
        return None

    users_by_id = payload.get("users_by_id", {})
    tweets_by_id = payload.get("tweets_by_id", {})
    author = users_by_id.get(str(tweet.get("author_id")), {}) if isinstance(users_by_id, dict) else {}
    author_username = c.collapse_ws(str(author.get("username", ""))) or "unknown"
    tweet_id = str(tweet.get("id", ""))
    canonical_url = f"https://x.com/{author_username}/status/{tweet_id}" if tweet_id else raw.source_url
    text = c.collapse_ws(str(tweet.get("text", "")))
    kind, title, excerpt, content_text = _tweet_details(tweet, author_username, text, users_by_id, tweets_by_id)

    return c._build_item(
        source=source,
        raw=raw,
        kind=kind,
        external_id=tweet_id or None,
        canonical_url=canonical_url,
        title=title,
        author=author_username,
        published_at=published_at,
        excerpt=excerpt,
        content_text=content_text,
    )


def _tweet_details(tweet: dict[str, Any], author_username: str, text: str, users_by_id: dict[str, Any], tweets_by_id: dict[str, Any]) -> tuple[str, str, str, str]:
    c = _core()
    references = tweet.get("referenced_tweets", [])
    reference_map = {
        str(entry.get("type")): tweets_by_id.get(str(entry.get("id")))
        for entry in references
        if isinstance(entry, dict) and entry.get("type")
    }
    reply_target = next((entry for entry in references if isinstance(entry, dict) and entry.get("type") == "replied_to"), None)

    retweeted = reference_map.get("retweeted")
    if isinstance(retweeted, dict):
        original_author = users_by_id.get(str(retweeted.get("author_id")), {}) if isinstance(users_by_id, dict) else {}
        original_username = c.collapse_ws(str(original_author.get("username", ""))) or "unknown"
        original_text = c.collapse_ws(str(retweeted.get("text", "")))
        title = f"@{author_username} reposted @{original_username}"
        excerpt = c.trim_text(original_text or title, 280)
        content = " ".join(bit for bit in [title, original_text] if bit)
        return "x-repost", c.trim_text(title, 200), excerpt, content

    quoted = reference_map.get("quoted")
    if isinstance(quoted, dict):
        quoted_author = users_by_id.get(str(quoted.get("author_id")), {}) if isinstance(users_by_id, dict) else {}
        quoted_username = c.collapse_ws(str(quoted_author.get("username", ""))) or "unknown"
        quoted_text = c.collapse_ws(str(quoted.get("text", "")))
        title = f"@{author_username} quoted @{quoted_username}"
        excerpt = c.trim_text(text or quoted_text or title, 280)
        content = " ".join(bit for bit in [title, text, f"Quoted @{quoted_username}", quoted_text] if bit)
        return "x-quote", c.trim_text(title, 200), excerpt, content

    if tweet.get("in_reply_to_user_id") or reply_target is not None:
        reply_user = users_by_id.get(str(tweet.get("in_reply_to_user_id")), {}) if isinstance(users_by_id, dict) else {}
        reply_username = c.collapse_ws(str(reply_user.get("username", "")))
        title = f"@{author_username} replied"
        if reply_username:
            title = f"@{author_username} replied to @{reply_username}"
        excerpt = c.trim_text(text or title, 280)
        content = " ".join(bit for bit in [title, text] if bit)
        return "x-reply", c.trim_text(title, 200), excerpt, content

    title = text or f"@{author_username} posted on X"
    excerpt = c.trim_text(text or title, 280)
    content = " ".join(bit for bit in [f"@{author_username}", text] if bit)
    return "x-post", c.trim_text(title, 200), excerpt, content


def _build_me_url() -> str:
    return f"{X_API_BASE}/2/users/me?user.fields=id,username,name"


def _build_timeline_url(user_id: str, *, max_results: int) -> str:
    c = _core()
    url = f"{X_API_BASE}/2/users/{user_id}/timelines/reverse_chronological"
    params = {
        "max_results": str(max_results),
        "expansions": "author_id,referenced_tweets.id,referenced_tweets.id.author_id,in_reply_to_user_id",
        "tweet.fields": "author_id,created_at,conversation_id,in_reply_to_user_id,referenced_tweets,text",
        "user.fields": "id,name,username",
    }
    for key, value in params.items():
        url = c._set_query_param(url, key, value)
    return url
