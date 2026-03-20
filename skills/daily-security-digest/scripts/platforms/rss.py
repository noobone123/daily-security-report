from __future__ import annotations

from typing import Any
from xml.etree import ElementTree


def _core():
    import core

    return core


def validate_fetch(path, kind: str, fetch: dict[str, str]) -> None:
    c = _core()
    if kind == "rss" and not fetch.get("url"):
        raise c.ValidationError(f"{path}: rss requires 'url'")


def fetch_raw_records(source, *, client, fetched_at):
    c = _core()
    url = source.fetch["url"]
    xml_text = client.get_text(url)
    entries = _parse_feed_entries(xml_text)
    max_items = c._fetch_int(source.fetch, "max_items", 20)
    rows = []
    for index, entry in enumerate(entries[:max_items]):
        seed = str(entry.get("id") or entry.get("link") or index)
        rows.append(
            c.RawRecord(
                raw_id=c.stable_id(source.id, "rss", seed),
                source_id=source.id,
                fetched_at=fetched_at,
                source_url=url,
                payload=entry,
            )
        )
    return rows


def normalize_raw_records(source, raw_records, *, window):
    c = _core()
    items = []
    for raw in raw_records:
        item = raw.payload
        published_at = c.parse_datetime(item.get("published_at")) or raw.fetched_at
        if not c._in_window(published_at, window):
            continue
        title = str(item.get("title") or item.get("summary") or "RSS item")
        excerpt = str(item.get("summary") or item.get("content") or title)
        link = str(item.get("link") or raw.source_url)
        items.append(
            c._build_item(
                source=source,
                raw=raw,
                kind="rss-item",
                external_id=str(item.get("id") or link),
                canonical_url=link,
                title=c.trim_text(title, 180),
                author=item.get("author"),
                published_at=published_at,
                excerpt=c.trim_text(excerpt, 280),
                content_text=" ".join(bit for bit in (title, excerpt) if bit),
            )
        )
    return items


def _parse_feed_entries(xml_text: str) -> list[dict[str, Any]]:
    c = _core()
    root = ElementTree.fromstring(xml_text)
    if c._local_name(root.tag) == "rss":
        channel = next((child for child in root if c._local_name(child.tag) == "channel"), None)
        if channel is None:
            return []
        items = [child for child in channel if c._local_name(child.tag) == "item"]
        return [_rss_item_to_dict(item) for item in items]
    if c._local_name(root.tag) == "feed":
        return [_atom_item_to_dict(entry) for entry in root if c._local_name(entry.tag) == "entry"]
    raise c.FetchError("Unsupported feed format")


def _rss_item_to_dict(item: ElementTree.Element) -> dict[str, Any]:
    c = _core()
    payload: dict[str, Any] = {}
    for child in item:
        name = c._local_name(child.tag)
        text = c.collapse_ws(child.text or "")
        if name == "link":
            payload["link"] = text
        elif name == "guid":
            payload["id"] = text
        elif name in {"description", "content"}:
            payload["summary"] = text
        elif name in {"pubDate", "date"}:
            payload["published_at"] = text
        elif name in {"author", "creator"}:
            payload["author"] = text
        elif text and name not in payload:
            payload[name] = text
    return payload


def _atom_item_to_dict(entry: ElementTree.Element) -> dict[str, Any]:
    c = _core()
    payload: dict[str, Any] = {}
    for child in entry:
        name = c._local_name(child.tag)
        if name == "link":
            href = child.attrib.get("href")
            if href:
                payload["link"] = href
        elif name == "author":
            name_node = next((node for node in child if c._local_name(node.tag) == "name"), None)
            if name_node is not None and name_node.text:
                payload["author"] = c.collapse_ws(name_node.text)
        elif name == "updated":
            payload["published_at"] = c.collapse_ws(child.text or "")
        elif name == "summary":
            payload["summary"] = c.collapse_ws(child.text or "")
        elif child.text:
            payload[name] = c.collapse_ws(child.text)
    return payload
