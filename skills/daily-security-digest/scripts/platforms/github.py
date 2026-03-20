from __future__ import annotations

from typing import Any

GITHUB_API_BASE = "https://api.github.com"
GITHUB_AUTHENTICATED_HANDLE = "@authenticated"
ALLOWED_GITHUB_USER_EVENTS = {
    "ReleaseEvent",
    "CreateEvent",
    "PushEvent",
    "PullRequestEvent",
    "WatchEvent",
}
ALLOWED_GITHUB_FEED_EVENTS = {
    "ReleaseEvent",
    "CreateEvent",
    "PushEvent",
    "PullRequestEvent",
    "WatchEvent",
    "IssuesEvent",
    "IssueCommentEvent",
    "ForkEvent",
    "PublicEvent",
    "MemberEvent",
}


def _core():
    import core

    return core


def validate_fetch(path, kind: str, fetch: dict[str, str]) -> None:
    c = _core()
    if kind == "github_user" and not (fetch.get("handle") or fetch.get("events_url")):
        raise c.ValidationError(f"{path}: github_user requires 'handle' or 'events_url'")
    if kind == "github_feed" and not fetch.get("handle"):
        raise c.ValidationError(f"{path}: github_feed requires 'handle'")


def fetch_raw_records(source, *, client, fetched_at):
    c = _core()
    if source.kind == "github_user":
        return _fetch_github_user_records(source, client=client, fetched_at=fetched_at)
    if source.kind == "github_feed":
        return _fetch_github_feed_records(source, client=client, fetched_at=fetched_at)
    raise c.FetchError(f"Unsupported GitHub source kind: {source.kind}")


def normalize_raw_records(source, raw_records, *, window):
    items = []
    for raw in raw_records:
        item = _normalize_record(source, raw, window=window)
        if item is not None:
            items.append(item)
    return items


def _fetch_github_user_records(source, *, client, fetched_at):
    c = _core()
    handle = source.fetch.get("handle", "")
    url = source.fetch.get("events_url") or f"{GITHUB_API_BASE}/users/{handle}/events/public"
    max_events = c._fetch_int(source.fetch, "max_events", 30)
    payload = client.get_json(url)
    if not isinstance(payload, list):
        raise c.FetchError(f"{source.id}: expected JSON list from {url}")
    rows = []
    for event in payload[:max_events]:
        event_id = str(event.get("id", len(rows)))
        rows.append(
            c.RawRecord(
                raw_id=c.stable_id(source.id, "github_user", event_id),
                source_id=source.id,
                fetched_at=fetched_at,
                source_url=url,
                payload=event,
            )
        )
    return rows


def _fetch_github_feed_records(source, *, client, fetched_at):
    c = _core()
    handle = _resolve_github_feed_handle(source, client=client)
    requested_max_events = c._fetch_int(source.fetch, "max_events", 100)
    max_events = min(requested_max_events, 100)
    base_url = source.fetch.get("feed_url") or f"{GITHUB_API_BASE}/users/{handle}/received_events"
    url = c._set_query_param(base_url, "per_page", str(max_events))
    payload = client.get_json(url)
    if not isinstance(payload, list):
        raise c.FetchError(f"{source.id}: expected JSON list from {url}")
    rows = []
    for event in payload[:max_events]:
        event_id = str(event.get("id", len(rows)))
        rows.append(
            c.RawRecord(
                raw_id=c.stable_id(source.id, "github_feed", event_id),
                source_id=source.id,
                fetched_at=fetched_at,
                source_url=url,
                payload=event,
            )
        )
    return rows


def _resolve_github_feed_handle(source, *, client) -> str:
    c = _core()
    configured_handle = c.collapse_ws(source.fetch.get("handle", ""))
    if not client.github_token:
        raise c.FetchError(f"{source.id}: github_feed requires GITHUB_TOKEN")
    payload = client.get_json(f"{GITHUB_API_BASE}/user")
    if not isinstance(payload, dict):
        raise c.FetchError(f"{source.id}: expected JSON object from {GITHUB_API_BASE}/user")
    authenticated_login = c.collapse_ws(str(payload.get("login", "")))
    if not authenticated_login:
        raise c.FetchError(f"{source.id}: authenticated GitHub user response missing 'login'")
    if configured_handle and configured_handle != GITHUB_AUTHENTICATED_HANDLE and configured_handle.lower() != authenticated_login.lower():
        raise c.FetchError(
            f"{source.id}: github_feed handle '{configured_handle}' does not match authenticated GitHub user "
            f"'{authenticated_login}'. Use github_user for public profile events."
        )
    return authenticated_login


def _normalize_record(source, raw, *, window):
    if source.kind == "github_user":
        return _normalize_github_event(source, raw, window=window, allowed_events=ALLOWED_GITHUB_USER_EVENTS)
    if source.kind == "github_feed":
        return _normalize_github_event(source, raw, window=window, allowed_events=ALLOWED_GITHUB_FEED_EVENTS)
    return None


def _normalize_github_event(source, raw, *, window, allowed_events: set[str]):
    c = _core()
    event = raw.payload
    event_type = str(event.get("type"))
    if event_type not in allowed_events:
        return None
    published_at = c.parse_datetime(event.get("created_at")) or raw.fetched_at
    if not c._in_window(published_at, window):
        return None
    repo_name = str(event.get("repo", {}).get("name", ""))
    actor = str(event.get("actor", {}).get("login") or source.fetch.get("handle") or "")
    payload = event.get("payload", {})
    canonical_url = f"https://github.com/{repo_name}" if repo_name else raw.source_url

    details = _github_event_details(event_type, repo_name, actor, payload, canonical_url)
    if details is None:
        return None
    canonical_url, title, excerpt, content_bits = details

    return c._build_item(
        source=source,
        raw=raw,
        kind=event_type.lower(),
        external_id=str(event.get("id")) if event.get("id") else None,
        canonical_url=canonical_url,
        title=title or repo_name or source.title,
        author=actor or None,
        published_at=published_at,
        excerpt=c.trim_text(excerpt or title, 280),
        content_text=" ".join(bit for bit in content_bits if bit),
    )


def _github_event_details(
    event_type: str,
    repo_name: str,
    actor: str,
    payload: dict[str, Any],
    default_url: str,
) -> tuple[str, str, str, list[str]] | None:
    c = _core()
    canonical_url = default_url
    title = f"{repo_name} update".strip()
    excerpt = ""
    content_bits: list[str] = []

    if event_type == "ReleaseEvent":
        release = payload.get("release", {})
        tag_name = str(release.get("tag_name") or release.get("name") or "new release")
        canonical_url = str(release.get("html_url") or canonical_url)
        title = f"{repo_name} released {tag_name}".strip()
        excerpt = c.collapse_ws(release.get("body") or f"{repo_name} published release {tag_name}.")
        content_bits.extend([title, excerpt, str(release.get("name", ""))])
    elif event_type == "CreateEvent":
        ref_type = str(payload.get("ref_type") or "resource")
        ref_name = str(payload.get("ref") or repo_name)
        title = f"{repo_name} created {ref_type} {ref_name}".strip()
        if ref_type == "tag" and repo_name and ref_name:
            canonical_url = f"https://github.com/{repo_name}/releases/tag/{ref_name}"
        excerpt = f"{actor} created {ref_type} {ref_name} in {repo_name}.".strip()
        content_bits.extend([title, excerpt])
    elif event_type == "PushEvent":
        head = payload.get("head")
        before = payload.get("before")
        if repo_name and before and head:
            canonical_url = f"https://github.com/{repo_name}/compare/{before}...{head}"
        commit_count = len(payload.get("commits", []))
        first_message = payload.get("commits", [{}])[0].get("message", "") if commit_count else ""
        title = f"{repo_name} pushed {commit_count} commit{'s' if commit_count != 1 else ''}".strip()
        excerpt = c.collapse_ws(first_message or f"{actor} pushed updates to {repo_name}.")
        content_bits.extend([title, excerpt])
    elif event_type == "PullRequestEvent":
        pr = payload.get("pull_request", {})
        action = str(payload.get("action") or "updated")
        canonical_url = str(pr.get("html_url") or canonical_url)
        title = c.collapse_ws(f"{repo_name} pull request {action}: {pr.get('title', '')}")
        excerpt = c.collapse_ws(pr.get("body") or pr.get("title") or title)
        content_bits.extend([title, excerpt])
    elif event_type == "WatchEvent":
        title = f"{actor} starred {repo_name}".strip()
        excerpt = f"{actor} starred {repo_name}, which may signal a noteworthy project or release.".strip()
        content_bits.extend([title, excerpt])
    elif event_type == "IssuesEvent":
        issue = payload.get("issue", {})
        action = str(payload.get("action") or "updated")
        canonical_url = str(issue.get("html_url") or canonical_url)
        title = c.collapse_ws(f"{repo_name} issue {action}: {issue.get('title', '')}")
        excerpt = c.collapse_ws(issue.get("body") or issue.get("title") or title)
        content_bits.extend([title, excerpt])
    elif event_type == "IssueCommentEvent":
        comment = payload.get("comment", {})
        issue = payload.get("issue", {})
        canonical_url = str(comment.get("html_url") or canonical_url)
        title = c.collapse_ws(f"Comment on {repo_name}: {issue.get('title', '')}")
        excerpt = c.collapse_ws(comment.get("body") or "")
        content_bits.extend([title, excerpt])
    elif event_type == "ForkEvent":
        forkee = payload.get("forkee", {})
        fork_name = str(forkee.get("full_name", ""))
        title = f"{actor} forked {repo_name}".strip()
        excerpt = f"{actor} forked {repo_name} to {fork_name}.".strip()
        content_bits.extend([title, excerpt])
    elif event_type == "PublicEvent":
        title = f"{repo_name} was made public".strip()
        excerpt = f"Repository {repo_name} has been made public by {actor}.".strip()
        content_bits.extend([title, excerpt])
    elif event_type == "MemberEvent":
        member = payload.get("member", {})
        member_login = str(member.get("login", ""))
        action = str(payload.get("action") or "added")
        title = f"{member_login} {action} as collaborator to {repo_name}".strip()
        excerpt = f"{member_login} was {action} as a collaborator to {repo_name} by {actor}.".strip()
        content_bits.extend([title, excerpt])
    else:
        return None

    return canonical_url, title, excerpt, content_bits
