---
name: daily-security-digest
description: Fetch security sources into local Markdown materials and let the calling agent write a digest from files.
---

# Daily Security Digest

Use this skill as a thin file-fetching layer for another agent.

The skill does not call an LLM, rank items, promote sources, or write the final summary.
It only fetches enabled sources, writes readable local Markdown files, and leaves the final `report.md` to the calling agent.

## Workflow

1. Collect materials for one day:

```bash
python3 skills/daily-security-digest/scripts/collect_materials.py \
  --workspace . \
  --date YYYY-MM-DD \
  --timezone Asia/Shanghai
```

2. Read the generated files under `data/runs/YYYY-MM-DD/`:

- `index.md`
- `items/*.md`
- `manifest.json`

3. Read the planning files and generated output, then write `report.md` yourself:
   - `planning/topics.md` — what to focus on per topic (care about, ignore, angle)
   - `planning/report-style.md` — audience, language, and format preferences
   - `data/runs/YYYY-MM-DD/index.md` — run overview and item list
   - `data/runs/YYYY-MM-DD/items/*.md` — full content per item

## Planning Files

The user-edited planning files live in:

- `planning/sources.toml` — source definitions (one `[[sources]]` block per source)
- `planning/topics.md` — topic definitions (one `## Heading` per topic)
- `planning/report-style.md` — report style preferences

The collector reads `sources.toml` and `topics.md`; `report-style.md` is for the calling agent only.
