from __future__ import annotations

import os
import re
from html import unescape
from typing import Any
from urllib.parse import urljoin, urlparse

from core import (
    FetchError,
    HttpClient,
    collapse_ws,
    parse_datetime,
    slugify,
    trim_text,
)


_ATTR_PATTERN = re.compile(r'([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*("([^"]*)"|\'([^\']*)\'|([^\s>]+))')


def resolve_source(raw_input: str, user_label: str = "", *, client: HttpClient | None = None) -> dict[str, Any]:
    value = raw_input.strip()
    if not value:
        raise ValueError("input is required")

    if _is_github_home(value):
        return {
            "id": "github-home",
            "title": user_label or "GitHub Home Feed",
            "kind": "github_feed",
            "enabled": True,
            "fetch": {"handle": "@authenticated"},
            "notes": "Authenticated GitHub home feed; requires GITHUB_TOKEN",
        }

    if _is_x_url(value):
        raise ValueError("X/Twitter sources are no longer supported")

    github_handle = _github_handle_from_input(value)
    if github_handle:
        return {
            "id": slugify(github_handle),
            "title": user_label or github_handle,
            "kind": "github_user",
            "enabled": True,
            "fetch": {"handle": github_handle},
            "notes": "",
        }

    if _looks_like_feed_url(value):
        title = user_label or _title_from_url(value)
        return {
            "id": slugify(title),
            "title": title,
            "kind": "rss",
            "enabled": True,
            "fetch": {"url": value},
            "notes": "RSS feed provided directly",
        }

    if _looks_like_url(value):
        http = client or HttpClient(github_token=os.environ.get("GITHUB_TOKEN"))
        try:
            html = http.get_text(value)
        except Exception as exc:  # noqa: BLE001
            return {
                "id": slugify(user_label or _title_from_url(value)),
                "title": user_label or _title_from_url(value),
                "kind": "web",
                "enabled": True,
                "fetch": {"url": value},
                "notes": f"RSS detection failed, will use platform-native web collector: {exc}",
            }
        title = user_label or _extract_title(html) or _title_from_url(value)
        feed_url = _discover_feed_url(html, value)
        if feed_url:
            return {
                "id": slugify(title),
                "title": title,
                "kind": "rss",
                "enabled": True,
                "fetch": {"url": feed_url},
                "notes": f"RSS feed auto-detected at {feed_url}",
            }
        return {
            "id": slugify(title),
            "title": title,
            "kind": "web",
            "enabled": True,
            "fetch": {"url": value},
            "notes": "No RSS found, will use platform-native web collector",
        }

    return {
        "id": slugify(value),
        "title": user_label or value,
        "kind": "github_user",
        "enabled": True,
        "fetch": {"handle": value},
        "notes": "",
    }


def _extract_title(html: str) -> str:
    for pattern in (
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']title["\'][^>]+content=["\']([^"\']+)["\']',
        r"<title[^>]*>(.*?)</title>",
        r"<h1[^>]*>(.*?)</h1>",
    ):
        match = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return collapse_ws(unescape(_strip_tags(match.group(1))))
    return ""


def _discover_feed_url(html: str, base_url: str) -> str | None:
    for attrs in _iter_tag_attrs(html, "link"):
        rel = attrs.get("rel", "").lower()
        media_type = attrs.get("type", "").lower()
        href = attrs.get("href", "")
        if "alternate" in rel and media_type in {"application/rss+xml", "application/atom+xml"} and href:
            return urljoin(base_url, href)
    return None


def _extract_link(html: str, *, rel: str, base_url: str) -> str | None:
    for attrs in _iter_tag_attrs(html, "link"):
        if rel in attrs.get("rel", "").lower() and attrs.get("href"):
            return urljoin(base_url, attrs["href"])
    return None


def _extract_published_at(html: str) -> str | None:
    for attrs in _iter_tag_attrs(html, "meta"):
        key = f"{attrs.get('property', '')}:{attrs.get('name', '')}".lower()
        if any(marker in key for marker in ("published_time", "pubdate", "timestamp", "date")):
            value = attrs.get("content", "")
            if parse_datetime(value):
                return value
    for attrs in _iter_tag_attrs(html, "time"):
        value = attrs.get("datetime", "")
        if parse_datetime(value):
            return value
    return None


def _strip_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html)


def _iter_tag_attrs(html: str, tag: str) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for match in re.finditer(rf"<{tag}\b(?P<attrs>[^>]*)>", html, flags=re.IGNORECASE):
        attrs: dict[str, str] = {}
        for key, _, double_quoted, single_quoted, bare in _ATTR_PATTERN.findall(match.group("attrs")):
            attrs[key.lower()] = double_quoted or single_quoted or bare
        results.append(attrs)
    return results


def _is_github_home(value: str) -> bool:
    if not _looks_like_url(value):
        return False
    parsed = urlparse(value)
    return parsed.netloc.lower() == "github.com" and parsed.path.rstrip("/") == ""


def _is_x_url(value: str) -> bool:
    if not _looks_like_url(value):
        return False
    parsed = urlparse(value)
    return parsed.netloc.lower() in {"x.com", "twitter.com", "www.x.com", "www.twitter.com"}


def _github_handle_from_input(value: str) -> str | None:
    if _looks_like_url(value):
        parsed = urlparse(value)
        if parsed.netloc.lower() != "github.com":
            return None
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) == 1:
            return parts[0]
        return None
    if re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37})", value):
        return value
    return None


def _looks_like_feed_url(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in ("/feed", "/rss", "/atom")) or lowered.endswith(".xml")


def _looks_like_url(value: str) -> bool:
    return bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value))


def _title_from_url(value: str) -> str:
    parsed = urlparse(value)
    host = parsed.netloc or value
    path = parsed.path.strip("/")
    if path:
        return collapse_ws(f"{host} {path.replace('/', ' ')}")
    return host
