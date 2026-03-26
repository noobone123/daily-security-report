from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tomllib
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from platforms import SUPPORTED_SOURCE_KINDS, adapter_for

ALLOWED_SOURCE_KINDS = set(SUPPORTED_SOURCE_KINDS)
GITHUB_API_VERSION = "2022-11-28"
RSS_DISABLED_MESSAGE = (
    "RSS/Atom sources are no longer supported. "
    "Use a website homepage or section page as a 'web' source instead."
)


class ValidationError(ValueError):
    """Raised when workspace planning files are invalid."""


class FetchError(RuntimeError):
    """Raised when a remote fetch fails."""


PLANNING_TEMPLATE_MAP = {
    "sources.toml": "sources.toml.example",
    "topics.md": "topics.md.example",
    "report-style.md": "report-style.md.example",
}
CONFIG_FILENAME = "config.toml"


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
class WorkspacePaths:
    root: Path
    config_path: Path

    @property
    def planning_dir(self) -> Path:
        return self.root / "planning"

    @property
    def runs_dir(self) -> Path:
        return self.root / "data" / "runs"

    def to_payload(self) -> dict[str, str]:
        return {
            "workspace": str(self.root),
            "workspace_config_path": str(self.config_path),
            "planning_dir": str(self.planning_dir),
            "runs_dir": str(self.runs_dir),
        }


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
        return self.request_text(url)

    def request_text(self, url: str, *, headers: dict[str, str] | None = None) -> str:
        merged_headers = self._headers_for(url)
        if headers:
            merged_headers.update(headers)
        request = Request(url, headers=merged_headers)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except (HTTPError, URLError) as exc:
            raise FetchError(f"Failed to fetch {url}: {exc}") from exc

    def get_json(self, url: str) -> Any:
        return self.request_json(url)

    def request_json(self, url: str, *, headers: dict[str, str] | None = None) -> Any:
        return json.loads(self.request_text(url, headers=headers))

    def _headers_for(self, url: str) -> dict[str, str]:
        host = urlparse(url).netloc
        headers = {"User-Agent": self.user_agent}
        if host == "api.github.com":
            headers["Accept"] = "application/vnd.github+json"
            headers["X-GitHub-Api-Version"] = GITHUB_API_VERSION
        else:
            headers["Accept"] = "application/json, text/html, application/xhtml+xml, application/xml;q=0.9"
        if self.github_token and host == "api.github.com":
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


def config_path_for_templates(templates_dir: Path) -> Path:
    return templates_dir.parent / CONFIG_FILENAME


def write_workspace_config(config_path: Path, workspace_root: Path) -> dict[str, str]:
    root = workspace_root.resolve()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        f'workspace_root = "{_escape_toml(str(root))}"\n',
        encoding="utf-8",
    )
    return {
        "workspace": str(root),
        "workspace_config_path": str(config_path.resolve()),
    }


def read_workspace_config(config_path: Path) -> WorkspacePaths:
    resolved_config = config_path.resolve()
    if not resolved_config.is_file():
        raise ValidationError(
            f"Missing workspace config: {resolved_config}. Run ./scripts/claude_install.sh or create the config file described in README."
        )
    with resolved_config.open("rb") as f:
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as exc:
            raise ValidationError(f"{resolved_config}: TOML parse error: {exc}") from exc
    raw_root = str(data.get("workspace_root", "")).strip()
    if not raw_root:
        raise ValidationError(f"{resolved_config}: workspace_root is required")
    root = Path(raw_root)
    if not root.is_absolute():
        raise ValidationError(f"{resolved_config}: workspace_root must be an absolute path")
    resolved_root = root.resolve()
    if not resolved_root.exists() or not resolved_root.is_dir():
        raise ValidationError(f"{resolved_config}: workspace_root does not exist: {resolved_root}")
    return WorkspacePaths(root=resolved_root, config_path=resolved_config)


def bootstrap_planning(templates_dir: Path) -> dict[str, Any]:
    workspace = read_workspace_config(config_path_for_templates(templates_dir))
    planning_dir = workspace.planning_dir
    planning_dir.mkdir(parents=True, exist_ok=True)

    created: list[str] = []
    existing: list[str] = []
    for target_name, template_name in PLANNING_TEMPLATE_MAP.items():
        template_path = templates_dir / template_name
        if not template_path.is_file():
            raise ValidationError(f"Missing planning template: {template_path}")
        target_path = planning_dir / target_name
        if target_path.exists():
            existing.append(str(target_path))
            continue
        shutil.copyfile(template_path, target_path)
        created.append(str(target_path))

    return {
        **workspace.to_payload(),
        "created": created,
        "existing": existing,
    }


def load_workspace(workspace_root: Path) -> WorkspaceSpec:
    paths = WorkspacePaths(root=workspace_root.resolve(), config_path=Path(""))
    planning_dir = paths.planning_dir
    sources_path = planning_dir / "sources.toml"
    report_style_path = planning_dir / "report-style.md"

    if not report_style_path.exists():
        raise ValidationError(f"Missing report style file: {report_style_path}")

    sources = load_all_sources(sources_path) if sources_path.is_file() else []
    report_style = load_report_style(report_style_path)
    return WorkspaceSpec(root=paths.root, sources=sources, report_style=report_style)


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
        if kind == "rss":
            raise ValidationError(f"{path}: {RSS_DISABLED_MESSAGE}")
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


def run_collection(templates_dir: Path, *, date_slug: str, timezone: str, days: int | None = None) -> dict[str, Any]:
    paths = read_workspace_config(config_path_for_templates(templates_dir))
    workspace = load_workspace(paths.root)
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
    seen_item_keys = load_seen_item_keys(workspace.root, exclude_date=date_slug)
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
    if any(s.kind == "github_feed" for s in script_sources) and not client.github_token:
        warnings.append("GITHUB_TOKEN is required for github_feed sources. Set the environment variable for authenticated GitHub home feed access.")
    for source in script_sources:
        try:
            raw_records = fetch_raw_records(source, client=client, fetched_at=fetched_at)
            raw_count += len(raw_records)
            items.extend(normalize_raw_records(source, raw_records, window=window))
        except Exception as exc:  # noqa: BLE001
            failures.append({"source_id": source.id, "error": str(exc)})
    deduped_items = sort_items(dedupe_items(items))
    # Cross-run dedup: filter out items already collected in previous runs
    if seen_item_keys:
        deduped_items = [item for item in deduped_items if _item_dedupe_key(item) not in seen_item_keys]
    collected_urls = [item.canonical_url for item in deduped_items if item.canonical_url]
    collected_item_keys = [_item_dedupe_key(item) for item in deduped_items]
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
        **paths.to_payload(),
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
        "collected_item_keys": collected_item_keys,
        "seen_urls": sorted(seen_urls),
        "seen_item_keys": sorted(seen_item_keys),
        "item_files": item_files,
        "agent_sources": [
            {"id": s.id, "title": s.title, "url": s.fetch.get("url", "")}
            for s in agent_sources
        ],
    }
    write_json(run_dir / "manifest.json", manifest)
    return manifest

def dedupe_items(items: list[CollectedItem]) -> list[CollectedItem]:
    deduped: dict[str, CollectedItem] = {}
    for item in items:
        key = _item_dedupe_key(item)
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


def load_seen_item_keys(workspace_root: Path, *, exclude_date: str | None = None) -> set[str]:
    """Collect dedupe keys from historical manifests, with URL fallback for older runs."""
    seen: set[str] = set()
    for manifest in _scan_previous_manifests(workspace_root, exclude_date=exclude_date):
        keys = manifest.get("collected_item_keys")
        if isinstance(keys, list):
            for key in keys:
                seen.add(str(key))
            continue
        for url in manifest.get("collected_urls", []):
            seen.add(str(url))
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
    if kind == "rss":
        raise ValidationError(f"{path}: {RSS_DISABLED_MESSAGE}")
    if kind == "web" and not fetch.get("url"):
        raise ValidationError(f"{path}: web source requires 'url'")
    if kind == "web":
        return
    adapter_for(kind).validate_fetch(path, kind, fetch)

def fetch_raw_records(source: SourceSpec, *, client: HttpClient, fetched_at: datetime) -> list[RawRecord]:
    return adapter_for(source.kind).fetch_raw_records(source, client=client, fetched_at=fetched_at)


def normalize_raw_records(source: SourceSpec, raw_records: list[RawRecord], *, window: TimeWindow) -> list[CollectedItem]:
    return adapter_for(source.kind).normalize_raw_records(source, raw_records, window=window)


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


def _item_dedupe_key(item: CollectedItem) -> str:
    if item.kind == "watchevent" and item.external_id:
        return f"event:{item.external_id}"
    if item.kind in {"x-post", "x-repost", "x-reply", "x-quote"} and item.external_id:
        return f"tweet:{item.external_id}"
    return item.canonical_url or item.external_id or f"{item.source_id}:{item.title.lower()}"


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


def _set_query_param(url: str, key: str, value: str) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query[key] = value
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


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
    if kind == "rss":
        raise ValidationError(RSS_DISABLED_MESSAGE)
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
