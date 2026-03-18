---
name: daily-security-digest
description: Fetch security sources into local Markdown materials and let the calling agent filter and write a digest from files.
---

# Daily Security Digest

Use this skill as a thin file-fetching layer for another agent.

The skill script handles API and RSS sources (structured data, deterministic, zero token cost).
Web sources are left to the calling agent via WebFetch (handles JS-rendered pages, complex layouts).

## Workflow

### Step 0: Source & Topic Setup (agent, interactive)

Before collecting, check if this is a first-time run or if the user wants to manage sources.

**File bootstrap**: First, ensure user config files exist (they are gitignored):
- If `planning/sources.toml` does not exist → copy from `planning/sources.toml.example`
- If `planning/topics.md` does not exist → copy from `planning/topics.md.example`

**First-time detection**: If `planning/sources.toml` has zero `[[sources]]` entries
OR all entries have `enabled = false`, treat this as first-time setup.

**Source onboarding flow**:
1. Read `planning/sources.toml` and present current sources to the user
2. Ask the user if they want to add any URLs
3. For each URL the user provides:
   a. Use WebFetch to load the page
   b. Look for RSS/Atom feed indicators:
      - `<link rel="alternate" type="application/rss+xml" ...>` in the HTML
      - Try common feed paths: `/feed`, `/rss`, `/atom.xml`, `/feed.xml`, `/index.xml`
   c. If RSS found → `kind = "rss"`, `fetch.url = <feed URL>`
   d. If no RSS → `kind = "web"`, `fetch.url = <original URL>`
   e. For GitHub user profiles (`github.com/<user>`) → `kind = "github_user"`,
      `fetch.handle = <user>`,
      `fetch.events_url = https://api.github.com/users/<user>/received_events/public`
   f. Generate a slugified `id` and a descriptive `title`
   g. Append the new `[[sources]]` block to `planning/sources.toml`
4. Confirm the final source list with the user

**Topic onboarding**: If `planning/topics.md` has no `##` headings (only comments),
ask the user what topics they care about, then write `## Topic Name` sections with
`### Care About`, `### Usually Ignore`, and `### Reporting Angle` sub-sections.

**Ongoing management**: The user can ask to add, remove, enable, or disable sources
at any time. Read `planning/sources.toml`, make the requested change, and write it back.

### Step 1: Collect API/RSS sources (script)

```bash
python3 scripts/collect_materials.py \
  --workspace . \
  --timezone Asia/Shanghai
```

`--date` defaults to today; override with `--date YYYY-MM-DD` if needed.

**Time range**: By default the script auto-continues from the last run's end time.
If no previous runs exist, it looks back 3 days. If the user asks for a specific range
(e.g., "last 7 days of news"), pass `--days 7` to override.

Outputs under `data/runs/YYYY-MM-DD/`:
- `manifest.json` — run metadata, collected item list, and `agent_sources` to fetch
- `index.md` — run overview with basic summaries
- `items/*.md` — one file per collected item

**Check warnings**: Read `manifest.json` → `warnings` array. If non-empty, inform the user
(e.g., "GITHUB_TOKEN not set, rate limit may apply"). Also check `failures` for any
sources that could not be collected, and report them to the user.

### Step 2: Collect web sources (agent)

Read `data/runs/YYYY-MM-DD/manifest.json` → `agent_sources` array.
For each entry, use WebFetch to fetch the URL and save an item file:

```
data/runs/YYYY-MM-DD/items/<source-id>-<slug>.md
```

Use the same item format (see below). Sub-agents can parallelize this step.

**Cross-run dedup**: Before writing a web-source item file, check if its URL appears in
`manifest.json` → `seen_urls`. Skip items whose URL is already there — they were collected
in a previous run.

**Error handling**: If WebFetch fails for any agent source (timeout, HTTP error, empty page),
record the failure and continue with the remaining sources. After all agent sources are
attempted, report any failures to the user with the source name and reason.

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

### Step 6: Send report summary to user (agent)

After writing `report.md`, send the user a message containing:
1. A concise summary of the report highlights (key findings, number of items, notable sources)
2. The full path to the report file: `data/runs/YYYY-MM-DD/report.md`

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
| `rss` | Script | RSS/Atom feed (XML) |
| `web` | Agent | Any web page — agent uses WebFetch |

## Notes

- The skill bundles its own test suite under `tests/` for development. The calling agent does **not** need to run tests during normal use.
