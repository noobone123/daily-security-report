# Daily Security Digest

Daily Security Digest packages a Claude Code workflow for collecting GitHub feeds, RSS, and web sources, then writing a filtered daily report. This repository is the plugin root and the single source of truth for the skill and its subagents.

## Distribution

There are two supported ways to use this project:

1. Plugin distribution through Claude Code
2. `scripts/claude_install.sh` as a fallback when plugin loading is not available

Do not use both for the same workspace, or Claude will see duplicate skills and subagents.

## Canonical Layout

```text
daily-security-report/
├── .claude-plugin/
│   └── plugin.json
├── agents/
│   ├── source-resolver.md
│   ├── web-source-collector.md
│   ├── item-filter.md
│   └── report-writer.md
├── skills/
│   └── daily-security-digest/
│       ├── SKILL.md
│       ├── scripts/
│       │   ├── bootstrap_planning.py
│       │   ├── collect_materials.py
│       │   └── skill_lib.py
│       └── templates/
│           ├── sources.toml.example
│           ├── topics.md.example
│           └── report-style.md.example
├── scripts/
│   └── claude_install.sh
└── tests/
```

`planning/` and `data/runs/` are runtime workspace state. They are created in the fixed repo workspace declared by `skills/daily-security-digest/config.toml`, not in the installed skill directory.

## Plugin Install

Plugin install is the official distribution path.

For local development, load the repository directly:

```bash
claude --plugin-dir /absolute/path/to/daily-security-report
```

The skill will then be available as:

```text
/daily-security-report:daily-security-digest
```

When you later publish this repository to a plugin marketplace, the same layout can be shipped as-is.

## Fallback Install Script

If the target environment cannot use plugins directly, install the same canonical files into Claude's standalone directories:

```bash
./scripts/claude_install.sh
```

This also writes the local workspace config file:

```text
skills/daily-security-digest/config.toml
```

Per the official docs, personal skills live at `~/.claude/skills/<skill-name>/SKILL.md` and personal subagents live at `~/.claude/agents/`. This fallback installs:

```text
/home/<user>/.claude/skills/daily-security-digest
/home/<user>/.claude/agents/source-resolver.md
/home/<user>/.claude/agents/web-source-collector.md
/home/<user>/.claude/agents/item-filter.md
/home/<user>/.claude/agents/report-writer.md
```

The fallback installer uses symlinks by default so the repository remains the only maintained source. If symlink creation is unavailable in the target environment, rerun with `--copy` as a last resort.

To install into another Claude directory for testing:

```bash
./scripts/claude_install.sh --claude-dir /tmp/my-claude
```

For plugin-only usage, initialize only the workspace config:

```bash
./scripts/claude_install.sh --config-only
```

After a fallback install, the skill is available as:

```text
/daily-security-digest
```

## Workflow

The skill follows a 6-step workflow:

| Step | Who | What |
|------|-----|------|
| -1 | Script | Bootstrap `planning/` files from bundled templates |
| 0 | Agent | Source and topic onboarding |
| 1 | Script | Collect GitHub and RSS items |
| 2 | Agent | Fetch web-only sources with parallel subagents |
| 3 | Agent | Summarize and filter items with parallel subagents |
| 4 | Agent | Write `report.md` |
| 5 | Agent | Deliver highlights to the user |

On first run, the skill creates these workspace files if they do not already exist:

- `planning/sources.toml`
- `planning/topics.md`
- `planning/report-style.md`

### Workspace Configuration

The runtime workspace is fixed by this local config file:

```text
skills/daily-security-digest/config.toml
```

Example:

```toml
workspace_root = "/absolute/path/to/daily-security-report"
```

This means:

- the current repo is the only supported workspace
- skill code may load from `~/.claude/skills/...` or a plugin directory, but runtime data is always written under the configured repo root
- plugin and fallback install share the same workspace config source

The scripts return these fields so the caller can tell the user exactly where data will go:

- `workspace`
- `workspace_config_path`
- `planning_dir`
- `runs_dir`

## Configuration

### Sources

Each source is a `[[sources]]` block in `planning/sources.toml`:

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
| `web` | Agent | `url` |

### Topics

`planning/topics.md` uses one `## Topic` section per area of interest. Each topic must include:

- `### Care About`
- `### Usually Ignore`
- `### Reporting Angle`

`## All` catch-all sections are not supported.

### Report Style

`planning/report-style.md` must contain:

- `## Audience`
- `## Language`
- `## Output Format`
- `## Extra Instructions`

## Environment

| Variable | Required | Purpose |
|----------|----------|---------|
| `GITHUB_TOKEN` | No | Raises GitHub API rate limits for `github_user` sources |

## Manual Script Run

```bash
python3 skills/daily-security-digest/scripts/collect_materials.py \
  --timezone Asia/Shanghai
```

Optional flags:

- `--date YYYY-MM-DD`
- `--days N`

## Tests

```bash
PYTHONPATH=skills/daily-security-digest/scripts \
python3 -m unittest discover -s tests -v
```

## Requirements

- Claude Code 1.0.33+ for plugin support
- Python 3.11+
- No third-party Python dependencies
