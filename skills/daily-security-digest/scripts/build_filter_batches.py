#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ItemHeader:
    path: Path
    title: str
    source_id: str
    source_title: str
    published_at: datetime
    url: str


def parse_item_header(path: Path) -> ItemHeader:
    text = path.read_text(encoding="utf-8")
    title = ""
    current_heading: str | None = None
    sections: dict[str, list[str]] = {}

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and not title:
            title = stripped[2:].strip()
            continue
        if stripped.startswith("## "):
            current_heading = stripped[3:].strip()
            sections.setdefault(current_heading, [])
            continue
        if current_heading is None:
            continue
        if current_heading == "Content":
            break
        sections[current_heading].append(line.rstrip())

    if not title:
        raise ValueError(f"{path}: missing '# <title>'")

    source_block = _section_text(path, sections, "Source")
    published_text = _section_text(path, sections, "Published At")
    url = _section_text(path, sections, "URL")
    source_title, source_id = _parse_source_block(path, source_block)
    published_at = _parse_timestamp(path, published_text)
    return ItemHeader(
        path=path.resolve(),
        title=title,
        source_id=source_id,
        source_title=source_title,
        published_at=published_at,
        url=url,
    )


def build_batches(
    items: list[ItemHeader],
    *,
    source_batch_threshold: int = 30,
    chunk_size: int = 10,
) -> list[dict[str, Any]]:
    if source_batch_threshold < 1:
        raise ValueError("source_batch_threshold must be >= 1")
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")

    grouped: dict[str, list[ItemHeader]] = {}
    source_titles: dict[str, str] = {}
    for item in items:
        grouped.setdefault(item.source_id, []).append(item)
        source_titles[item.source_id] = item.source_title

    batches: list[dict[str, Any]] = []
    for source_id in sorted(grouped):
        ordered = sorted(
            grouped[source_id],
            key=lambda item: (-item.published_at.timestamp(), str(item.path)),
        )
        groups = [ordered]
        if len(ordered) > source_batch_threshold:
            groups = [ordered[index:index + chunk_size] for index in range(0, len(ordered), chunk_size)]
        for batch_index, group in enumerate(groups):
            batches.append(
                {
                    "source_id": source_id,
                    "source_title": source_titles[source_id],
                    "batch_index": batch_index,
                    "item_paths": [str(item.path) for item in group],
                }
            )
    return batches


def build_batches_for_run(
    *,
    workspace: Path,
    run_date: str,
    source_batch_threshold: int = 30,
    chunk_size: int = 10,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    items_dir = workspace / "data" / "runs" / run_date / "items"
    if not items_dir.is_dir():
        raise ValueError(f"Missing items directory: {items_dir}")
    item_paths = sorted(items_dir.glob("*.md"))
    headers = [parse_item_header(path) for path in item_paths]
    batches = build_batches(
        headers,
        source_batch_threshold=source_batch_threshold,
        chunk_size=chunk_size,
    )
    return {
        "ok": True,
        "workspace": str(workspace),
        "run_date": run_date,
        "items_dir": str(items_dir),
        "item_count": len(headers),
        "batch_count": len(batches),
        "source_batch_threshold": source_batch_threshold,
        "chunk_size": chunk_size,
        "batches": batches,
    }


def _section_text(path: Path, sections: dict[str, list[str]], heading: str) -> str:
    lines = sections.get(heading)
    if not lines:
        raise ValueError(f"{path}: missing '## {heading}'")
    text = " ".join(line.strip() for line in lines if line.strip()).strip()
    if not text:
        raise ValueError(f"{path}: empty '## {heading}'")
    return text


def _parse_source_block(path: Path, value: str) -> tuple[str, str]:
    marker = " (`"
    if marker not in value or not value.endswith("`)"):
        raise ValueError(f"{path}: invalid Source block: {value!r}")
    source_title, source_id = value.rsplit(marker, 1)
    source_id = source_id[:-2]
    return source_title.strip(), source_id.strip()


def _parse_timestamp(path: Path, value: str) -> datetime:
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{path}: invalid Published At timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build source-scoped filter batches for a run.")
    parser.add_argument("--workspace", required=True, help="Absolute or relative workspace root")
    parser.add_argument("--run-date", required=True, help="Run date YYYY-MM-DD")
    parser.add_argument("--source-threshold", type=int, default=30, help="Use one batch per source when item count is <= threshold")
    parser.add_argument("--chunk-size", type=int, default=10, help="Chunk size for sources above the threshold")
    args = parser.parse_args(argv)
    try:
        payload = build_batches_for_run(
            workspace=Path(args.workspace),
            run_date=args.run_date,
            source_batch_threshold=args.source_threshold,
            chunk_size=args.chunk_size,
        )
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=False))
        return 1
    print(json.dumps(payload, indent=2, sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
