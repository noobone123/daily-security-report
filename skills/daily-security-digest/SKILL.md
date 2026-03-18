---
name: daily-security-digest
description: Fetch security sources into local Markdown materials and let the calling agent filter and write a digest from files.
---

# Daily Security Digest

Use this skill as a thin file-fetching layer for another agent.

The skill script handles API and RSS sources (structured data, deterministic, zero token cost).
Web sources are left to the calling agent via WebFetch (handles JS-rendered pages, complex layouts).

## Workflow

### Step 1: Collect API/RSS sources (script)

```bash
python3 skills/daily-security-digest/scripts/collect_materials.py \
  --workspace . \
  --timezone Asia/Shanghai
```

`--date` defaults to today; override with `--date YYYY-MM-DD` if needed.

Outputs under `data/runs/YYYY-MM-DD/`:
- `manifest.json` — run metadata, collected item list, and `agent_sources` to fetch
- `index.md` — run overview with basic summaries
- `items/*.md` — one file per collected item

### Step 2: Collect web sources (agent)

Read `data/runs/YYYY-MM-DD/manifest.json` → `agent_sources` array.
For each entry, use WebFetch to fetch the URL and save an item file:

```
data/runs/YYYY-MM-DD/items/<source-id>-<slug>.md
```

Use the same item format (see below). Sub-agents can parallelize this step.

### Step 3: Build index with LLM summaries (agent)

Read ALL `data/runs/YYYY-MM-DD/items/*.md` and rewrite `index.md` with a 2-3 sentence
LLM-generated summary per item. This replaces the basic rule-based summaries from Step 1.

### Step 4: Filter by topic relevance (agent)

- Read `planning/topics.md` for topic guidance (care about, ignore, reporting angle)
- Read `index.md` and select items that match your topics
- Copy selected item files into `data/runs/YYYY-MM-DD/filtered/`

### Step 5: Write report (agent)

- Read `planning/report-style.md` for audience, language, and format preferences
- Write `report.md` based only on the items in `filtered/`

## Item File Format

Every item file (whether written by the script or the agent) uses this format:

```markdown
# <title>

## Source
<source title> (`<source-id>`)

## Published At
<ISO 8601 timestamp>

## URL
<canonical URL>

## Summary
<2-3 sentence summary>

## Content
<full text content>
```

## Planning Files

The user-edited planning files live in:

- `planning/sources.toml` — source definitions (one `[[sources]]` block per source)
- `planning/topics.md` — topic guidance for the calling agent (one `## Heading` per topic)
- `planning/report-style.md` — report style preferences for the calling agent

The collector reads only `sources.toml`; `topics.md` and `report-style.md` are for the calling agent.

## Source Kinds

| Kind | Handled by | Notes |
|------|-----------|-------|
| `github_user` | Script | GitHub user event feed (API JSON) |
| `github_repo` | Script | Repo releases + tags (API JSON) |
| `rss` | Script | RSS/Atom feed (XML) |
| `web` | Agent | Any web page — agent uses WebFetch |
