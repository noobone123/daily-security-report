---
name: daily-security-digest
description: Fetch security sources into local Markdown materials and let the calling agent filter and write a digest from files.
disable-model-invocation: true
allowed-tools: WebFetch, Agent, Read, Write, Glob, Grep, Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/bootstrap_planning.py *), Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/collect_materials.py *)
argument-hint: "[date] [--days N]"
---

# Daily Security Digest

Use this skill as a thin file-fetching layer for another agent.

The skill script handles API and RSS sources (structured data, deterministic, zero token cost).
Web sources are left to the calling agent via WebFetch (handles JS-rendered pages, complex layouts).

## Workflow

### Step -1: Bootstrap planning files (script)

Before checking the workspace state, make sure the planning files exist.

Run:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/bootstrap_planning.py \
  --workspace .
```

This creates `planning/sources.toml`, `planning/topics.md`, and
`planning/report-style.md` from the skill's bundled templates, without
overwriting any file that already exists.

### Step 0: Source & Topic Onboarding (agent, interactive — BLOCKING GATE)

> **MANDATORY STOP**: This step requires multiple rounds of user interaction.
> You MUST NOT proceed to Step 1 until the user has provided their own sources
> AND their own topics AND confirmed both.

> **FORBIDDEN ACTIONS** (violating any invalidates the entire setup):
> - NEVER populate sources.toml with URLs from your training data or general knowledge
> - NEVER use `## All` or any catch-all heading in topics.md
> - NEVER write to sources.toml or topics.md before the user has replied with content
> - NEVER generate topic names, "Care About" lists, or "Usually Ignore" lists yourself
> - NEVER proceed to Step 1 if the user has not provided at least one URL or username
> - NEVER treat example file comments as valid content

**First-time detection** — this is first-time setup if ANY of these are true:
1. `planning/sources.toml` has zero `[[sources]]` entries, or all have `enabled = false`
2. `planning/topics.md` has no `##` headings (only comments)
3. `planning/topics.md` has a `## All` catch-all heading
4. Any topic in `planning/topics.md` lacks `### Care About` or `### Usually Ignore`

If first-time setup is detected, run all three phases below.

---

**Phase 1 — Ask for sources**:

Ask the user:

> "Which security blogs, news sites, or GitHub profiles do you want to follow?
> Please share URLs or GitHub usernames."

**STOP HERE.** Output the question above. Do NOT call any tools. Do NOT write any
files. Do NOT continue. End your response and wait for the user's reply.

---

**Phase 2 — Resolve and confirm sources**:

When the user replies with URLs or usernames:
1. Launch one **`source-resolver`** subagent per input in a single message (parallel).
   Each subagent receives `{input, user_label}` and returns a JSON classification.
2. Collect all results, present the resolved source list to the user.
3. Ask: "Does this look right? Anything to add, remove, or change?"

**STOP HERE.** Do NOT write to `planning/sources.toml` yet. Wait for the user to confirm.

When the user confirms → write all sources to `planning/sources.toml`.

---

**Phase 3 — Ask for topics**:

If topics.md needs setup (first-time conditions 2-4 above):

Ask the user:

> "What security topics do you care about most? (e.g. cloud security, exploit dev,
> threat intel, detection engineering...)"

**STOP HERE.** Output the question. Do NOT write to topics.md. Wait for the user's reply.

When the user replies with topics:
1. For each topic the user names, ask: "What specifically do you care about within
   \<topic\>? And what should I ignore?"
2. **STOP HERE.** Wait for the user's answers.
3. Only after the user answers → write `## Topic Name` sections with
   `### Care About`, `### Usually Ignore`, and `### Reporting Angle` sub-sections
   using the user's exact words.

---

**Gate check before Step 1** — verify ALL of the following before proceeding:
- `planning/sources.toml` has at least one `[[sources]]` entry with `enabled = true`
- Every source URL traces back to a user message in this conversation
- `planning/topics.md` has at least one `## Topic` heading (not `## All`)
- Every topic has non-empty `### Care About` and `### Usually Ignore`

If any check fails, re-run the relevant phase above.

### Step 1: Collect API/RSS sources (script)

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/collect_materials.py \
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

### Step 2: Collect web sources (parallel subagents)

Read `data/runs/YYYY-MM-DD/manifest.json` → `agent_sources` array.
If there are no agent_sources, skip to Step 3.

**Launch one `web-source-collector` subagent per web source** using the Agent tool.
Issue all Agent tool calls in a single message so they run in parallel.

Each subagent receives:
```
source_id, source_title, source_url, run_date, workspace, seen_urls
```
It writes one item file and returns the path (or `SKIPPED: ...` / `FAILED: ...`).

After all subagents complete:
- Collect results and report any failures to the user with source name and reason
- Proceed to Step 3

**Error handling**: If a subagent fails (WebFetch timeout, HTTP error, empty page),
it should return a failure result rather than crashing. The main agent continues
with the remaining items.

### Step 3: Summarize and filter by topic (parallel subagents)

Read `planning/topics.md` for topic guidance.
List all `data/runs/YYYY-MM-DD/items/*.md` files.

**Batch items into groups of approximately 10.** For each batch, launch an **`item-filter`**
subagent using the Agent tool. Issue all Agent tool calls in a single message so they run
in parallel.

Each subagent receives:
```
item_paths, topics_path, workspace, run_date
```
It writes summaries into item files and returns a JSON array of `{path, relevant, topics, title, url}`.

After all subagents complete, the main agent:
1. Rewrites `data/runs/YYYY-MM-DD/index.md` with the LLM summaries (replaces the
   script-generated basic summaries)
2. Copies relevant item files to `data/runs/YYYY-MM-DD/filtered/`
3. If zero items are relevant, inform the user and ask whether to relax the filter
   or skip the report

### Step 4: Write report (agent)

Launch the **`report-writer`** subagent with:
```
run_date, workspace, report_style_path=planning/report-style.md
```
It reads all `filtered/*.md` files and writes `data/runs/<run_date>/report.md`.

### Step 5: Deliver report to user (agent)

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

## Source Management (user-initiated only)

The user can ask to add, remove, enable, or disable sources at any time.
Read `planning/sources.toml`, make the requested change, and write it back.

**This applies ONLY when the user explicitly requests a specific change**
(e.g., "add this URL", "remove source X", "disable the-record"). It MUST NOT
be used to bypass Step 0 onboarding or to auto-populate sources.

## Notes

- The skill bundles its own test suite under `tests/` for development. The calling agent does **not** need to run tests during normal use.
- The bundled templates live under `${CLAUDE_SKILL_DIR}/templates/` so the skill remains self-contained in both plugin and standalone installs.
- **Optional optimization**: After Step 1, you may launch Step 2 subagents (web fetch) and Step 3 subagents (for script-collected items only) in the same message. When Step 2 completes, launch additional Step 3 subagents for web-collected items. This overlaps fetching with summarization but requires careful result merging.
