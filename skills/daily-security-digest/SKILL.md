---
name: daily-security-digest
description: Fetch security sources into local Markdown materials and let the calling agent filter and write a digest from files.
disable-model-invocation: true
allowed-tools: Agent, Read, Write, Glob, Grep, Bash
argument-hint: "[date] [--days N]"
---

# Daily Security Digest

Use this workflow as a thin orchestration layer around shared Python scripts and a small number of LLM subagents.

GitHub API collection, source resolution, and web collection are all handled by shared Python CLIs so the same runtime works in Claude Code and Codex.
Only the source-scoped summarization and final report-writing steps rely on LLM subagents.

## Path Conventions

- `{skillDir}` — the directory containing this `SKILL.md`
- `{workspaceDir}` — the workspace root configured for runtime data. It contains `planning/` and `data/runs/`.
- `{skillDir}/scripts` — the directory containing the shared Python scripts for this workflow.

All data path references below are relative to `{workspaceDir}`.

## Workflow

### Step -1: Bootstrap planning files (script)

Before checking the workspace state, make sure the local workspace config exists and then make sure the planning files exist.

Run:

```bash
python3 {skillDir}/scripts/bootstrap_planning.py
```

This creates `{workspaceDir}/planning/sources.toml`, `{workspaceDir}/planning/topics.md`, and `{workspaceDir}/planning/report-style.md` from the bundled templates, without overwriting any file that already exists.

After running the script, read its JSON output and tell the user:
- the fixed workspace root (`workspace`)
- the workspace config file path (`workspace_config_path`)
- the planning directory (`planning_dir`)
- the run output directory (`runs_dir`)

### Step 0: Source & Topic Onboarding (interactive gate)

**Returning user shortcut** — read both planning files and check:
1. `{workspaceDir}/planning/sources.toml` has at least one `[[sources]]` entry with `enabled = true`
2. `{workspaceDir}/planning/topics.md` has at least one `##` heading that is not `## All`

If BOTH conditions are true, this is a returning user:
- Tell the user: `"Found <N> enabled sources, topics: [<heading1>, <heading2>, …]. Proceeding to collection."`
- Skip directly to Step 1. Do NOT run any onboarding phases.

If either condition is NOT met, this is first-time setup. Continue below.

---

> **MANDATORY STOP**: The onboarding flow below requires multiple rounds of user interaction.
> You MUST NOT proceed to Step 1 until the user has provided their own sources AND their own topics AND confirmed both.

> **FORBIDDEN ACTIONS** (apply only during first-time onboarding):
> - NEVER populate `sources.toml` with URLs from training data or general knowledge
> - NEVER use `## All` or any catch-all heading in `topics.md`
> - NEVER write to `sources.toml` or `topics.md` before the user has replied with content
> - NEVER generate topic names or topic content yourself
> - NEVER proceed to Step 1 if the user has not provided at least one URL or username
> - NEVER treat example file comments as valid content

**First-time detection** — this is first-time setup if ANY of these are true:
1. `{workspaceDir}/planning/sources.toml` has zero `[[sources]]` entries, or all have `enabled = false`
2. `{workspaceDir}/planning/topics.md` has no `##` headings (only comments)
3. `{workspaceDir}/planning/topics.md` has a `## All` catch-all heading

If first-time setup is detected, run all three phases below.

**Phase 1 — Ask for sources**:

Ask the user:

> "Which security blogs, news sites, or GitHub profiles do you want to follow?
> Please share URLs or GitHub usernames."

**STOP HERE.** Do NOT call tools. Do NOT write files. Wait for the user's reply.

**Phase 2 — Resolve and confirm sources**:

When the user replies with URLs or usernames:
1. Run `python3 {skillDir}/scripts/resolve_source.py --input "<value>"` once per input. Add `--user-label "<label>"` when the user provided a label.
   The resolver maps `github.com` to `github_feed`, GitHub usernames to `github_user`, rejects X/Twitter URLs, rejects RSS/Atom feeds, and resolves normal websites to `web`.
2. Collect all JSON results and present the resolved source list to the user.
3. Ask: "Does this look right? Anything to add, remove, or change?"

**STOP HERE.** Do NOT write to `{workspaceDir}/planning/sources.toml` yet. Wait for the user to confirm.

When the user confirms, write all sources to `{workspaceDir}/planning/sources.toml`.

**Phase 3 — Ask for topics**:

If `topics.md` needs setup (first-time conditions 2-3 above):

Ask the user:

> "What security topics do you care about most? (e.g. cloud security, exploit dev, threat intel, detection engineering...)"

**STOP HERE.** Do NOT write to `topics.md`. Wait for the user's reply.

When the user replies with topics:
1. For each topic the user names, ask: "What specifically do you care about within <topic>? And what should I ignore?"
2. **STOP HERE.** Wait for the user's answers.
3. Only after the user answers, write `## Topic Name` sections using the user's exact words. The user may organize content freely under each heading — do not enforce any particular sub-heading structure.

**Gate check before Step 1** — verify ALL of the following before proceeding:
- `{workspaceDir}/planning/sources.toml` has at least one `[[sources]]` entry with `enabled = true`
- `{workspaceDir}/planning/topics.md` has at least one `## Topic` heading (not `## All`)

If any check fails, re-run the relevant phase above.

### Step 1: Collect script-backed sources (script)

```bash
python3 {skillDir}/scripts/collect_materials.py \
  --timezone Asia/Shanghai
```

`--date` defaults to today. Override with `--date YYYY-MM-DD` if needed.

**Time range**: By default the script auto-continues from the last run's end time.
If no previous runs exist, it looks back 3 days. If the user asks for a specific range, pass `--days N`.

Outputs under `{workspaceDir}/data/runs/YYYY-MM-DD/`:
- `manifest.json` — run metadata, collected item list, and `agent_sources` to fetch
- `index.md` — run overview with basic summaries
- `items/*.md` — one file per collected item

**Check warnings**: Read `manifest.json` → `warnings` array. If non-empty, inform the user.
Also check `failures` for any sources that could not be collected, and report them to the user.
`github_feed` is a strict authenticated GitHub home feed and requires `GITHUB_TOKEN`.

Before moving on to Step 2, repeat the resolved write location to the user in one line:
- workspace root
- planning directory
- run output directory

### Step 2: Collect web sources (`MUST` using bounded-concurrency parallel subagents)

Read `{workspaceDir}/data/runs/YYYY-MM-DD/manifest.json` → `agent_sources` array.
If there are no `agent_sources`, skip to Step 3.

Before launching collectors, determine the concurrency cap `N`:

1. Read project `{workspaceDir}/.codex/config.toml` and use ``[agents].max_threads`` if present.
2. If project config is absent, unreadable, or does not set it, read global `~/.codex/config.toml` and use ``[agents].max_threads`` if present.
3. If neither config provides a usable value, assume `6`.

Treat `N` as the maximum number of concurrently open subagent threads for this step.

Each collector receives:

```text
source_id, source_title, source_url, run_date, workspace, seen_urls, max_hops=3, max_items=20
```

Collection rules:

- same-domain only
- max_hops = 3
- up to 20 items per source
- one collector handles one source end-to-end

Run this step as a rolling worker pool:

- Build a queue from `agent_sources`.
- Launch up to `N` `web-source-collector` subagents initially.
- Whenever one collector finishes, immediately launch the next queued source.
- If a spawn attempt fails with a thread-limit error, do not mark that source failed. Instead, treat it as temporary capacity exhaustion, requeue it, wait for an active collector to finish or close, and retry.
- Do not mark that source failed unless the collector itself runs and returns a real source-level failure.

Worked example:

- If there are 10 sources and `max_threads = 6`, launch the first 6 and keep the remaining 4 queued.
- As each running collector finishes, immediately launch the next queued source until the queue is empty.

Each collector should use the current platform's native web tooling to inspect the entry page, follow same-domain candidate links, and write high-signal content pages to `items/`.

Only after the queue is empty and all active collectors complete:
- merge their `written`, `skipped`, and `failed` results
- report any failures to the user with source name and reason
- proceed to Step 3

### Step 3: Summarize and filter by topic (`MUST` using bounded-concurrency parallel subagents)

Read `{workspaceDir}/planning/topics.md` for topic guidance.
Run:

```bash
python3 {skillDir}/scripts/build_filter_batches.py \
  --workspace "{workspaceDir}" \
  --run-date YYYY-MM-DD
```

This helper reads item headers only, groups items by `source_id`, and builds filter batches:
- if a source has `<= 30` items, emit one batch for that source
- if a source has `> 30` items, split it into source-scoped chunks of 10
- within a source, sort by `Published At` descending before batching

Before launching `item-filter` subagents, determine the same concurrency cap `N`:

1. Read project `{workspaceDir}/.codex/config.toml` and use ``[agents].max_threads`` if present.
2. If project config is absent, unreadable, or does not set it, read global `~/.codex/config.toml` and use ``[agents].max_threads`` if present.
3. If neither config provides a usable value, assume `6`.

For each returned batch, launch the installed `item-filter` subagent for the current platform with the same rolling worker-pool rule:

- Build a queue from the returned batches.
- Launch up to `N` batches initially.
- Whenever one batch finishes, immediately launch the next queued source-scoped batch.
- If a spawn attempt fails with a thread-limit error, do not mark that batch failed. Instead, treat it as temporary capacity exhaustion, requeue it, wait for an active subagent to finish or close, and retry.

Worked example:

- If there are 10 batches and `max_threads = 6`, launch the first 6 and keep the remaining 4 queued.
- As each running batch finishes, immediately launch the next queued batch until the queue is empty.

Each subagent receives:
```
source_id, source_title, item_paths, topics_path, workspace, run_date
```

Only after the queue is empty and all active `item-filter` subagents complete, the main agent:
1. Rewrites `{workspaceDir}/data/runs/YYYY-MM-DD/index.md` with the LLM summaries
2. Copies relevant item files to `{workspaceDir}/data/runs/YYYY-MM-DD/filtered/`
3. If zero items are relevant, inform the user and ask whether to relax the filter or skip the report

### Step 4: Write report (subagent)

Launch the installed `report-writer` subagent for the current platform with:
```
run_date, workspace, report_style_path={workspaceDir}/planning/report-style.md
```

It reads all `filtered/*.md` files and writes `{workspaceDir}/data/runs/<run_date>/report.md`.

### Step 5: Deliver report to user

After writing `report.md`, send the user a message containing:
1. A concise summary of the report highlights
2. The full path to the report file: `{workspaceDir}/data/runs/YYYY-MM-DD/report.md`

## Item File Format

Every item file uses this format:

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

- `{workspaceDir}/planning/sources.toml` — source definitions (one `[[sources]]` block per source)
- `{workspaceDir}/planning/topics.md` — topic guidance for the calling agent
- `{workspaceDir}/planning/report-style.md` — report style preferences for the calling agent

The collector reads only `sources.toml`; `topics.md` and `report-style.md` are for the calling agent.

## Source Kinds

| Kind | Handled by | Notes |
|------|-----------|-------|
| `github_user` | Script | Public GitHub profile event feed (API JSON) |
| `github_feed` | Script | Authenticated GitHub home feed (`fetch.handle = "@authenticated"`, requires `GITHUB_TOKEN`) |
| `web` | Parallel subagents | One `web-source-collector` per source, same-domain only, max_hops = 3, up to 20 items per source |

## Source Management (user-initiated only)

The user can ask to add, remove, enable, or disable sources at any time.
Read `{workspaceDir}/planning/sources.toml`, make the requested change, and write it back.

**This applies ONLY when the user explicitly requests a specific change**
(e.g. "add this URL", "remove source foo", "disable the-record"). It MUST NOT be used to bypass Step 0 onboarding or to auto-populate sources.

## Notes

- The workflow bundles its own test suite under `tests/` for development. The calling agent does not need to run tests during normal use.
- The bundled templates live under `{skillDir}/templates/`.
