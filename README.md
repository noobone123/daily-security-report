# Daily Security Digest

An [Agent Skill](https://agentskills.io) that collects security intelligence from GitHub feeds, RSS, and web sources, then generates a filtered daily digest report.

Works with [Claude Code](https://claude.ai/code) and any other Agent Skills-compatible tool.

## How It Works

The skill follows a 6-step workflow — a Python script handles structured APIs (zero token cost), and the calling agent handles everything else:

| Step | Who | What |
|------|-----|------|
| 0 | Agent | **Source Setup** — interactive onboarding, URL analysis, RSS detection |
| 1 | Script | **Collect API/RSS** — fetch GitHub events and RSS feeds |
| 2 | Agent | **Collect Web** — WebFetch each web source listed in `manifest.json` |
| 3 | Agent | **Build Index** — rewrite `index.md` with LLM-generated summaries |
| 4 | Agent | **Filter** — select items matching `planning/topics.md` |
| 5 | Agent | **Report** — write `report.md` per `planning/report-style.md` |

## Project Structure

```
daily-security-report/              # Workspace root (this repo)
├── skills/
│   └── daily-security-digest/      # The skill (source of truth)
│       ├── SKILL.md                # Skill instructions (agent reads this)
│       └── scripts/
│           ├── collect_materials.py # CLI entry point
│           └── skill_lib.py        # Core engine
├── planning/                       # User config (edit these)
│   ├── sources.toml                # Source definitions
│   ├── topics.md                   # Topic guidance for filtering
│   └── report-style.md            # Report style preferences
├── data/
│   └── runs/                       # Generated output (gitignored)
│       └── YYYY-MM-DD/
│           ├── manifest.json
│           ├── index.md
│           ├── items/*.md
│           ├── filtered/*.md
│           └── report.md
└── tests/
```

**Why this layout?** The [Agent Skills spec](https://agentskills.io/specification) defines a skill as a self-contained folder (`SKILL.md` + `scripts/` + `references/` + `assets/`). User-editable config (`planning/`) and generated output (`data/`) live outside the skill because they vary per user and per run. The script takes `--workspace .` to find the project root.

## Installation

Most Agent Skills-compatible tools auto-discover skills from a `.claude/skills/` (or equivalent) directory. Since this repo keeps the skill source at `skills/daily-security-digest/`, create a symlink so your agent can find it.

### Quick Start (this repo)

```bash
git clone <this-repo-url> daily-security-report
cd daily-security-report

# Create the symlink for agent auto-discovery
mkdir -p .claude/skills
ln -s ../../skills/daily-security-digest .claude/skills/daily-security-digest
```

Start your agent. The skill appears as `/daily-security-digest`.

On first run, the agent detects that all sample sources are disabled and walks you through interactive onboarding — just provide URLs, and it auto-detects RSS feeds vs. web pages vs. GitHub profiles.

### Install into Another Project

```bash
mkdir -p your-project/.claude/skills
ln -s /absolute/path/to/daily-security-report/skills/daily-security-digest \
      your-project/.claude/skills/daily-security-digest

# Copy planning templates (these are user-editable, not symlinked)
cp -r /absolute/path/to/daily-security-report/planning your-project/planning

# Create output directory
mkdir -p your-project/data/runs
```

### Install Globally (all projects)

```bash
mkdir -p ~/.claude/skills
ln -s /absolute/path/to/daily-security-report/skills/daily-security-digest \
      ~/.claude/skills/daily-security-digest
```

When installed globally, the skill is available everywhere but still needs `planning/` at the project root.

## Configuration

### Sources (`planning/sources.toml`)

Each source is a `[[sources]]` block:

```toml
[[sources]]
id = "my-rss-feed"
title = "My Security RSS"
kind = "rss"
enabled = true
fetch.url = "https://example.com/feed.xml"
```

| Kind | Collected by | Required `fetch` fields |
|------|-------------|------------------------|
| `github_user` | Script (API) | `handle` or `events_url` |
| `rss` | Script (XML) | `url` |
| `web` | Agent (WebFetch) | `url` |

You can edit `sources.toml` manually or let the agent manage it interactively — just ask it to "add a source" or "remove source X".

### Topics (`planning/topics.md`)

One `## Heading` per topic. The agent uses this to filter collected items in Step 4.

```markdown
# Topics

## All

Include all collected items in the report. Do not filter by topic.
```

To filter by specific topics, replace the `## All` section with one `## Heading` per topic describing what to look for.

### Report Style (`planning/report-style.md`)

Controls the report format. Required sections: Audience, Language, Output Format, Extra Instructions.

## Environment Variables

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `GITHUB_TOKEN` | No | (none) | GitHub API auth. Without it: 60 req/hr. With it: 5000 req/hr. |

## Running the Script Manually

```bash
python3 skills/daily-security-digest/scripts/collect_materials.py \
  --workspace . --timezone Asia/Shanghai
```

Optional: `--date YYYY-MM-DD` (defaults to today).

Output is JSON to stdout. Collected files go to `data/runs/YYYY-MM-DD/`.

## Running Tests

```bash
PYTHONPATH=skills/daily-security-digest/scripts \
  python3 -m unittest discover -s tests -v
```

## Requirements

- Python 3.11+ (uses `tomllib` from stdlib)
- No third-party Python dependencies
- Any [Agent Skills](https://agentskills.io)-compatible tool (Claude Code, VS Code Copilot, Cursor, Gemini CLI, etc.)
