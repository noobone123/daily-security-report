from __future__ import annotations

import hashlib
import json
import os
import re
import tomllib
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

ALLOWED_SOURCE_KINDS = {
    "github_user",
    "rss",
    "web",
}
ALLOWED_GITHUB_USER_EVENTS = {
    "ReleaseEvent",
    "CreateEvent",
    "PushEvent",
    "PullRequestEvent",
    "WatchEvent",
}


class ValidationError(ValueError):
    """Raised when workspace planning files are invalid."""


class FetchError(RuntimeError):
    """Raised when a remote fetch fails."""


@dataclass(slots=True)
class SourceSpec:
    id: str
    title: str
    kind: str
    enabled: bool
    fetch: dict[str, str]
    notes: str


@dataclass(slots=True)
class ReportStyle:
    title: str
    audience: str
    language: str
    output_format: str
    extra_instructions: str


@dataclass(slots=True)
class WorkspaceSpec:
    root: Path
    sources: list[SourceSpec]
    report_style: ReportStyle

    @property
    def enabled_sources(self) -> list[SourceSpec]:
        return [source for source in self.sources if source.enabled]


@dataclass(slots=True)
class TimeWindow:
    start: datetime
    end: datetime


@dataclass(slots=True)
class RawRecord:
    raw_id: str
    source_id: str
    fetched_at: datetime
    source_url: str
    payload: dict[str, Any]


@dataclass(slots=True)
class CollectedItem:
    item_id: str
    source_id: str
    kind: str
    external_id: str | None
    canonical_url: str
    title: str
    author: str | None
    published_at: datetime
    fetched_at: datetime
    excerpt: str
    content_text: str
    language: str

    def timestamp(self) -> datetime:
        return self.published_at or self.fetched_at


@dataclass(slots=True)
class HttpClient:
    github_token: str | None = None
    timeout: int = 20
    user_agent: str = "daily-security-digest/0.2"

    def get_text(self, url: str) -> str:
        request = Request(url, headers=self._headers_for(url))
        try:
            with urlopen(request, timeout=self.timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except (HTTPError, URLError) as exc:
            raise FetchError(f"Failed to fetch {url}: {exc}") from exc

    def get_json(self, url: str) -> Any:
        return json.loads(self.get_text(url))

    def _headers_for(self, url: str) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/html, application/xhtml+xml, application/xml;q=0.9",
            "User-Agent": self.user_agent,
        }
        if self.github_token and urlparse(url).netloc == "api.github.com":
            headers["Authorization"] = f"Bearer {self.github_token}"
        return headers


def validate_workspace(workspace_root: Path) -> dict[str, Any]:
    workspace = load_workspace(workspace_root)
    return {
        "ok": True,
        "workspace": str(workspace.root),
        "sources": len(workspace.sources),
        "enabled_sources": len(workspace.enabled_sources),
        "report_style": True,
    }


def load_workspace(workspace_root: Path) -> WorkspaceSpec:
    root = workspace_root.resolve()
    planning_dir = root / "planning"
    sources_path = planning_dir / "sources.toml"
    report_style_path = planning_dir / "report-style.md"

    if not report_style_path.exists():
        raise ValidationError(f"Missing report style file: {report_style_path}")

    sources = load_all_sources(sources_path) if sources_path.is_file() else []
    report_style = load_report_style(report_style_path)
    return WorkspaceSpec(root=root, sources=sources, report_style=report_style)


def load_all_sources(path: Path) -> list[SourceSpec]:
    with path.open("rb") as f:
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as exc:
            raise ValidationError(f"{path}: TOML parse error: {exc}") from exc
    entries = data.get("sources", [])
    if not entries:
        return []
    sources: list[SourceSpec] = []
    seen_ids: set[str] = set()
    for entry in entries:
        source_id = str(entry.get("id", "")).strip()
        if not source_id:
            raise ValidationError(f"{path}: a source is missing the 'id' field")
        if slugify(source_id) != source_id:
            raise ValidationError(f"{path}: source id '{source_id}' must be lowercase hyphen-case")
        if source_id in seen_ids:
            raise ValidationError(f"{path}: duplicate source id '{source_id}'")
        title = str(entry.get("title", "")).strip()
        if not title:
            raise ValidationError(f"{path}: source '{source_id}' is missing 'title'")
        kind = str(entry.get("kind", "")).strip().lower()
        if not kind:
            raise ValidationError(f"{path}: source '{source_id}' is missing 'kind'")
        if kind not in ALLOWED_SOURCE_KINDS:
            raise ValidationError(f"{path}: source '{source_id}' unsupported kind '{kind}'")
        enabled_raw = entry.get("enabled")
        if not isinstance(enabled_raw, bool):
            raise ValidationError(f"{path}: source '{source_id}' 'enabled' must be a boolean (true/false)")
        fetch = {k: str(v) for k, v in entry.get("fetch", {}).items()}
        _validate_fetch(path, kind, fetch)
        seen_ids.add(source_id)
        sources.append(SourceSpec(
            id=source_id,
            title=title,
            kind=kind,
            enabled=enabled_raw,
            fetch=fetch,
            notes=str(entry.get("notes", "")).strip(),
        ))
    return sources


def load_report_style(path: Path) -> ReportStyle:
    title, sections = _parse_sections(
        path,
        required_sections=("Audience", "Language", "Output Format", "Extra Instructions"),
    )
    if title != "Report Style":
        raise ValidationError(f"{path}: top heading must be '# Report Style'")
    return ReportStyle(
        title=title,
        audience=_section_text(path, "Audience", sections["Audience"]),
        language=_section_text(path, "Language", sections["Language"]),
        output_format=_section_text(path, "Output Format", sections["Output Format"]),
        extra_instructions=_section_text(path, "Extra Instructions", sections["Extra Instructions"]),
    )


def run_collection(workspace_root: Path, *, date_slug: str, timezone: str, days: int | None = None) -> dict[str, Any]:
    workspace = load_workspace(workspace_root)
    # Build time window: explicit --days > auto-continue from last run > 3-day default
    if days is not None:
        window = build_time_window(date_slug, timezone, days=days)
    else:
        last_end = find_last_window_end(workspace.root, exclude_date=date_slug)
        if last_end is not None:
            window = build_time_window(date_slug, timezone, since=last_end)
        else:
            window = build_time_window(date_slug, timezone, days=3)
    seen_urls = load_seen_urls(workspace.root, exclude_date=date_slug)
    client = HttpClient(github_token=os.environ.get("GITHUB_TOKEN"))
    fetched_at = datetime.now(tz=UTC)
    raw_count = 0
    failures: list[dict[str, str]] = []
    warnings: list[str] = []
    items: list[CollectedItem] = []
    script_sources = [s for s in workspace.enabled_sources if s.kind != "web"]
    agent_sources = [s for s in workspace.enabled_sources if s.kind == "web"]
    if any(s.kind == "github_user" for s in script_sources) and not client.github_token:
        warnings.append("GITHUB_TOKEN not set. GitHub API rate limit is 60 requests/hour. Set the environment variable for 5000 req/hr.")
    for source in script_sources:
        try:
            raw_records = fetch_raw_records(source, client=client, fetched_at=fetched_at)
            raw_count += len(raw_records)
            items.extend(normalize_raw_records(source, raw_records, window=window))
        except Exception as exc:  # noqa: BLE001
            failures.append({"source_id": source.id, "error": str(exc)})
    deduped_items = sort_items(dedupe_items(items))
    # Cross-run dedup: filter out items already collected in previous runs
    if seen_urls:
        deduped_items = [item for item in deduped_items if item.canonical_url not in seen_urls]
    collected_urls = [item.canonical_url for item in deduped_items if item.canonical_url]
    run_dir = workspace.root / "data" / "runs" / date_slug
    item_dir = run_dir / "items"
    _prepare_run_dir(item_dir)
    sources_by_id = {source.id: source for source in workspace.sources}
    item_files: list[str] = []
    for item in deduped_items:
        item_path = item_dir / f"{item.item_id}.md"
        item_path.write_text(render_item_markdown(item, sources_by_id[item.source_id], timezone), encoding="utf-8")
        item_files.append(str(item_path.relative_to(run_dir)))
    (run_dir / "index.md").write_text(
        render_index_markdown(
            date_slug=date_slug,
            timezone=timezone,
            workspace=workspace,
            window=window,
            items=deduped_items,
            failures=failures,
            warnings=warnings,
        ),
        encoding="utf-8",
    )
    manifest = {
        "date": date_slug,
        "timezone": timezone,
        "window_start": window.start.isoformat(),
        "window_end": window.end.isoformat(),
        "run_dir": str(run_dir.relative_to(workspace.root)),
        "source_count": len(workspace.sources),
        "enabled_source_count": len(workspace.enabled_sources),
        "raw_count": raw_count,
        "item_count": len(deduped_items),
        "failure_count": len(failures),
        "failures": failures,
        "warnings": warnings,
        "collected_urls": collected_urls,
        "seen_urls": sorted(seen_urls),
        "item_files": item_files,
        "agent_sources": [
            {"id": s.id, "title": s.title, "url": s.fetch.get("url", "")}
            for s in agent_sources
        ],
    }
    write_json(run_dir / "manifest.json", manifest)
    return manifest


def fetch_raw_records(source: SourceSpec, *, client: HttpClient, fetched_at: datetime) -> list[RawRecord]:
    if source.kind == "github_user":
        return _fetch_github_user_records(source, client=client, fetched_at=fetched_at)
    if source.kind == "rss":
        return _fetch_rss_records(source, client=client, fetched_at=fetched_at)
    raise FetchError(f"Unsupported source kind: {source.kind}")


def normalize_raw_records(source: SourceSpec, raw_records: list[RawRecord], *, window: TimeWindow) -> list[CollectedItem]:
    items: list[CollectedItem] = []
    for raw in raw_records:
        item = _normalize_record(source, raw, window=window)
        if item is not None:
            items.append(item)
    return items


def dedupe_items(items: list[CollectedItem]) -> list[CollectedItem]:
    deduped: dict[str, CollectedItem] = {}
    for item in items:
        key = item.canonical_url or item.external_id or f"{item.source_id}:{item.title.lower()}"
        existing = deduped.get(key)
        if existing is None or _item_quality(item) > _item_quality(existing):
            deduped[key] = item
    return list(deduped.values())


def sort_items(items: list[CollectedItem]) -> list[CollectedItem]:
    return sorted(items, key=lambda item: (-item.timestamp().timestamp(), item.source_id, item.item_id))


def render_index_markdown(
    *,
    date_slug: str,
    timezone: str,
    workspace: WorkspaceSpec,
    window: TimeWindow,
    items: list[CollectedItem],
    failures: list[dict[str, str]],
    warnings: list[str] | None = None,
) -> str:
    zone = ZoneInfo(timezone)
    lines = [
        f"# Collected Materials for {date_slug}",
        "",
        "## Run",
        "",
        f"- Date: {date_slug}",
        f"- Timezone: {timezone}",
        f"- Window Start: {window.start.astimezone(zone).isoformat()}",
        f"- Window End: {window.end.astimezone(zone).isoformat()}",
        f"- Enabled Sources: {len(workspace.enabled_sources)}",
        f"- Collected Items: {len(items)}",
        f"- Report Style: `planning/report-style.md`",
        "",
        "## Enabled Sources",
        "",
    ]
    if workspace.enabled_sources:
        for source in workspace.enabled_sources:
            lines.append(f"- {source.title} (`{source.id}`) | kind: {source.kind}")
    else:
        lines.append("- No enabled sources.")
    lines.extend(["", "## Failures", ""])
    if failures:
        for failure in failures:
            lines.append(f"- {failure['source_id']}: {failure['error']}")
    else:
        lines.append("- None.")
    lines.extend(["", "## Warnings", ""])
    actual_warnings = warnings or []
    if actual_warnings:
        for warning in actual_warnings:
            lines.append(f"- {warning}")
    else:
        lines.append("- None.")
    lines.extend(["", "## Items", ""])
    if not items:
        lines.append("- No items collected for this day.")
    else:
        sources_by_id = {source.id: source for source in workspace.sources}
        for item in items:
            source = sources_by_id[item.source_id]
            lines.extend(
                [
                    f"### [{item.title}]({item.canonical_url})",
                    "",
                    f"- Source: {source.title} (`{source.id}`)",
                    f"- Timestamp: {item.timestamp().astimezone(zone).isoformat()}",
                    f"- Summary: {build_summary(item)}",
                    f"- Item File: [items/{item.item_id}.md](items/{item.item_id}.md)",
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def render_item_markdown(item: CollectedItem, source: SourceSpec, timezone: str) -> str:
    lines = [
        f"# {item.title}",
        "",
        "## Source",
        "",
        f"{source.title} (`{source.id}`)",
        "",
        "## Published At",
        "",
        item.timestamp().astimezone(ZoneInfo(timezone)).isoformat(),
        "",
        "## URL",
        "",
        item.canonical_url,
        "",
        "## Summary",
        "",
        build_summary(item),
        "",
        "## Content",
        "",
        item.content_text or item.title,
        "",
    ]
    return "\n".join(lines)


def build_day_window(date_slug: str, timezone: str) -> TimeWindow:
    return build_time_window(date_slug, timezone)


def build_time_window(date_slug: str, timezone: str, *, since: datetime | None = None, days: int | None = None) -> TimeWindow:
    """Build a collection time window.

    - ``since`` given → use it as start, end = midnight after date_slug
    - ``days`` given  → start = end - days
    - neither         → single-day window (equivalent to days=1)
    """
    target_date = date.fromisoformat(date_slug)
    zone = ZoneInfo(timezone)
    end_local = datetime.combine(target_date + timedelta(days=1), time.min, tzinfo=zone)
    if since is not None:
        start = ensure_utc(since)
    elif days is not None:
        if days < 1:
            raise ValidationError("days must be >= 1")
        start = (end_local - timedelta(days=days)).astimezone(UTC)
    else:
        start = datetime.combine(target_date, time.min, tzinfo=zone).astimezone(UTC)
    return TimeWindow(start=start, end=end_local.astimezone(UTC))


def build_summary(item: CollectedItem) -> str:
    return trim_text(item.excerpt or item.content_text or item.title, 320)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def _scan_previous_manifests(workspace_root: Path, *, exclude_date: str | None = None) -> list[dict[str, Any]]:
    """Return parsed manifest dicts from previous runs, sorted by date."""
    runs_dir = workspace_root / "data" / "runs"
    if not runs_dir.is_dir():
        return []
    results: list[dict[str, Any]] = []
    for manifest_path in sorted(runs_dir.glob("*/manifest.json")):
        date_slug = manifest_path.parent.name
        if exclude_date and date_slug == exclude_date:
            continue
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            results.append(data)
        except (json.JSONDecodeError, OSError):
            continue
    return results


def load_seen_urls(workspace_root: Path, *, exclude_date: str | None = None) -> set[str]:
    """Collect all previously collected URLs from historical manifests."""
    seen: set[str] = set()
    for manifest in _scan_previous_manifests(workspace_root, exclude_date=exclude_date):
        for url in manifest.get("collected_urls", []):
            seen.add(url)
    return seen


def find_last_window_end(workspace_root: Path, *, exclude_date: str | None = None) -> datetime | None:
    """Find the most recent window_end from previous runs."""
    latest: datetime | None = None
    for manifest in _scan_previous_manifests(workspace_root, exclude_date=exclude_date):
        raw = manifest.get("window_end")
        if not raw:
            continue
        parsed = parse_datetime(raw)
        if parsed and (latest is None or parsed > latest):
            latest = parsed
    return latest


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value or "item"


def stable_id(*parts: str) -> str:
    seed = "::".join(part for part in parts if part)
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def collapse_ws(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def trim_text(value: str, limit: int = 220) -> str:
    value = collapse_ws(value)
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "..."


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _prepare_run_dir(item_dir: Path) -> None:
    item_dir.mkdir(parents=True, exist_ok=True)
    for path in item_dir.glob("*.md"):
        path.unlink()


def _parse_sections(path: Path, *, required_sections: tuple[str, ...]) -> tuple[str, dict[str, list[str]]]:
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise ValidationError(f"{path}: file cannot be empty")
    if text.lstrip().startswith("---"):
        raise ValidationError(f"{path}: YAML frontmatter is not supported")
    title: str | None = None
    sections: dict[str, list[str]] = {}
    current: str | None = None
    required = set(required_sections)
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and title is None:
            title = stripped[2:].strip()
            continue
        if stripped.startswith("## "):
            heading = stripped[3:].strip()
            if heading not in required:
                raise ValidationError(f"{path}: unsupported section heading '{heading}'")
            if heading in sections:
                raise ValidationError(f"{path}: duplicate section heading '{heading}'")
            sections[heading] = []
            current = heading
            continue
        if stripped.startswith("# ") and title is not None:
            raise ValidationError(f"{path}: only one top-level '# ' heading is allowed")
        if current is None:
            if stripped:
                raise ValidationError(f"{path}: unexpected content outside named sections")
            continue
        sections[current].append(line.rstrip())
    if not title:
        raise ValidationError(f"{path}: missing top-level '# ' heading")
    for heading in required_sections:
        if heading not in sections or not any(line.strip() for line in sections[heading]):
            raise ValidationError(f"{path}: missing required content for section '{heading}'")
    return title, sections


def _section_text(path: Path, heading: str, lines: list[str]) -> str:
    text = collapse_ws("\n".join(line.strip() for line in lines if line.strip()))
    if not text:
        raise ValidationError(f"{path}: section '{heading}' cannot be empty")
    return text


def _validate_fetch(path: Path, kind: str, fetch: dict[str, str]) -> None:
    if kind == "github_user" and not (fetch.get("handle") or fetch.get("events_url")):
        raise ValidationError(f"{path}: github_user requires 'handle' or 'events_url'")
    if kind == "rss" and not fetch.get("url"):
        raise ValidationError(f"{path}: rss requires 'url'")
    if kind == "web" and not fetch.get("url"):
        raise ValidationError(f"{path}: web source requires 'url'")


def _fetch_github_user_records(source: SourceSpec, *, client: HttpClient, fetched_at: datetime) -> list[RawRecord]:
    handle = source.fetch.get("handle", "")
    url = source.fetch.get("events_url") or f"https://api.github.com/users/{handle}/events/public"
    max_events = _fetch_int(source.fetch, "max_events", 30)
    payload = client.get_json(url)
    if not isinstance(payload, list):
        raise FetchError(f"{source.id}: expected JSON list from {url}")
    rows: list[RawRecord] = []
    for event in payload[:max_events]:
        event_id = str(event.get("id", len(rows)))
        rows.append(
            RawRecord(
                raw_id=stable_id(source.id, "github_user", event_id),
                source_id=source.id,
                fetched_at=fetched_at,
                source_url=url,
                payload=event,
            )
        )
    return rows


def _fetch_rss_records(source: SourceSpec, *, client: HttpClient, fetched_at: datetime) -> list[RawRecord]:
    url = source.fetch["url"]
    xml_text = client.get_text(url)
    entries = _parse_feed_entries(xml_text)
    max_items = _fetch_int(source.fetch, "max_items", 20)
    rows: list[RawRecord] = []
    for index, entry in enumerate(entries[:max_items]):
        seed = str(entry.get("id") or entry.get("link") or index)
        rows.append(
            RawRecord(
                raw_id=stable_id(source.id, "rss", seed),
                source_id=source.id,
                fetched_at=fetched_at,
                source_url=url,
                payload=entry,
            )
        )
    return rows


def _parse_feed_entries(xml_text: str) -> list[dict[str, Any]]:
    root = ElementTree.fromstring(xml_text)
    if _local_name(root.tag) == "rss":
        channel = next((child for child in root if _local_name(child.tag) == "channel"), None)
        if channel is None:
            return []
        items = [child for child in channel if _local_name(child.tag) == "item"]
        return [_rss_item_to_dict(item) for item in items]
    if _local_name(root.tag) == "feed":
        return [_atom_item_to_dict(entry) for entry in root if _local_name(entry.tag) == "entry"]
    raise FetchError("Unsupported feed format")


def _rss_item_to_dict(item: ElementTree.Element) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for child in item:
        name = _local_name(child.tag)
        text = collapse_ws(child.text or "")
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
    payload: dict[str, Any] = {}
    for child in entry:
        name = _local_name(child.tag)
        if name == "link":
            href = child.attrib.get("href")
            if href:
                payload["link"] = href
        elif name == "author":
            name_node = next((node for node in child if _local_name(node.tag) == "name"), None)
            if name_node is not None and name_node.text:
                payload["author"] = collapse_ws(name_node.text)
        elif name == "updated":
            payload["published_at"] = collapse_ws(child.text or "")
        elif name == "summary":
            payload["summary"] = collapse_ws(child.text or "")
        elif child.text:
            payload[name] = collapse_ws(child.text)
    return payload


def _normalize_record(source: SourceSpec, raw: RawRecord, *, window: TimeWindow) -> CollectedItem | None:
    if source.kind == "github_user":
        return _normalize_github_user_record(source, raw, window=window)
    if source.kind == "rss":
        return _normalize_rss_record(source, raw, window=window)
    return None


def _normalize_github_user_record(source: SourceSpec, raw: RawRecord, *, window: TimeWindow) -> CollectedItem | None:
    event = raw.payload
    event_type = str(event.get("type"))
    if event_type not in ALLOWED_GITHUB_USER_EVENTS:
        return None
    published_at = parse_datetime(event.get("created_at")) or raw.fetched_at
    if not _in_window(published_at, window):
        return None
    repo_name = str(event.get("repo", {}).get("name", ""))
    actor = str(event.get("actor", {}).get("login") or source.fetch.get("handle") or "")
    payload = event.get("payload", {})
    canonical_url = f"https://github.com/{repo_name}" if repo_name else raw.source_url
    title = f"{repo_name} update".strip()
    excerpt = ""
    content_bits: list[str] = []
    if event_type == "ReleaseEvent":
        release = payload.get("release", {})
        tag_name = str(release.get("tag_name") or release.get("name") or "new release")
        canonical_url = str(release.get("html_url") or canonical_url)
        title = f"{repo_name} released {tag_name}".strip()
        excerpt = collapse_ws(release.get("body") or f"{repo_name} published release {tag_name}.")
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
        excerpt = collapse_ws(first_message or f"{actor} pushed updates to {repo_name}.")
        content_bits.extend([title, excerpt])
    elif event_type == "PullRequestEvent":
        pr = payload.get("pull_request", {})
        action = str(payload.get("action") or "updated")
        canonical_url = str(pr.get("html_url") or canonical_url)
        title = collapse_ws(f"{repo_name} pull request {action}: {pr.get('title', '')}")
        excerpt = collapse_ws(pr.get("body") or pr.get("title") or title)
        content_bits.extend([title, excerpt])
    elif event_type == "WatchEvent":
        title = f"{actor} starred {repo_name}".strip()
        excerpt = f"{actor} starred {repo_name}, which may signal a noteworthy project or release.".strip()
        content_bits.extend([title, excerpt])
    return _build_item(
        source=source,
        raw=raw,
        kind=event_type.lower(),
        external_id=str(event.get("id")) if event.get("id") else None,
        canonical_url=canonical_url,
        title=title or repo_name or source.title,
        author=actor or None,
        published_at=published_at,
        excerpt=trim_text(excerpt or title, 280),
        content_text=" ".join(bit for bit in content_bits if bit),
    )


def _normalize_rss_record(source: SourceSpec, raw: RawRecord, *, window: TimeWindow) -> CollectedItem | None:
    item = raw.payload
    published_at = parse_datetime(item.get("published_at")) or raw.fetched_at
    if not _in_window(published_at, window):
        return None
    title = str(item.get("title") or item.get("summary") or "RSS item")
    excerpt = str(item.get("summary") or item.get("content") or title)
    link = str(item.get("link") or raw.source_url)
    return _build_item(
        source=source,
        raw=raw,
        kind="rss-item",
        external_id=str(item.get("id") or link),
        canonical_url=link,
        title=trim_text(title, 180),
        author=item.get("author"),
        published_at=published_at,
        excerpt=trim_text(excerpt, 280),
        content_text=" ".join(bit for bit in (title, excerpt) if bit),
    )


def _build_item(
    *,
    source: SourceSpec,
    raw: RawRecord,
    kind: str,
    external_id: str | None,
    canonical_url: str,
    title: str,
    author: str | None,
    published_at: datetime,
    excerpt: str,
    content_text: str,
) -> CollectedItem:
    item_id = stable_id(source.id, canonical_url, external_id or "", title)
    return CollectedItem(
        item_id=item_id,
        source_id=source.id,
        kind=kind,
        external_id=external_id,
        canonical_url=canonical_url,
        title=trim_text(title, 200),
        author=collapse_ws(author or "") or None,
        published_at=ensure_utc(published_at),
        fetched_at=ensure_utc(raw.fetched_at),
        excerpt=collapse_ws(excerpt),
        content_text=collapse_ws(content_text),
        language=_detect_language(f"{title} {excerpt} {content_text}"),
    )


def _fetch_int(fetch: dict[str, str], key: str, default: int) -> int:
    value = fetch.get(key)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValidationError(f"Fetch key '{key}' must be an integer") from exc
    if parsed <= 0:
        raise ValidationError(f"Fetch key '{key}' must be positive")
    return parsed


def _item_quality(item: CollectedItem) -> tuple[int, int, float]:
    return (len(item.content_text), len(item.excerpt), item.timestamp().timestamp())


def _detect_language(text: str) -> str:
    for char in text:
        if "\u4e00" <= char <= "\u9fff":
            return "zh"
    return "en"


def _in_window(timestamp: datetime, window: TimeWindow) -> bool:
    current = ensure_utc(timestamp)
    return window.start <= current < window.end


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _escape_toml(value: str) -> str:
    """Escape a string for use in a TOML double-quoted value."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def format_source_toml(
    *,
    source_id: str,
    title: str,
    kind: str,
    enabled: bool = True,
    notes: str = "",
    fetch: dict[str, str],
) -> str:
    """Return a single ``[[sources]]`` TOML block ready to append to sources.toml."""
    sid = slugify(source_id)
    if kind not in ALLOWED_SOURCE_KINDS:
        raise ValidationError(f"Unknown source kind: {kind!r}")
    _validate_fetch(Path("(generated)"), kind, {k: str(v) for k, v in fetch.items()})
    lines = [
        "[[sources]]",
        f'id = "{sid}"',
        f'title = "{_escape_toml(title)}"',
        f'kind = "{kind}"',
        f"enabled = {'true' if enabled else 'false'}",
    ]
    if notes:
        lines.append(f'notes = "{_escape_toml(notes)}"')
    for k, v in fetch.items():
        lines.append(f'fetch.{k} = "{_escape_toml(str(v))}"')
    return "\n".join(lines) + "\n"


def remove_source_block(sources_path: Path, source_id: str) -> bool:
    """Remove a ``[[sources]]`` block by id from a TOML file. Returns True if found."""
    text = sources_path.read_text(encoding="utf-8")
    blocks = re.split(r"(?=^\[\[sources\]\])", text, flags=re.MULTILINE)
    new_blocks = []
    removed = False
    for block in blocks:
        if not block.strip():
            continue
        if f'id = "{source_id}"' in block:
            removed = True
            continue
        new_blocks.append(block)
    if removed:
        sources_path.write_text(
            "\n".join(b.strip() for b in new_blocks) + "\n",
            encoding="utf-8",
        )
    return removed
