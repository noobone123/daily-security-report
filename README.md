# Daily Security Digest

Daily Security Digest 会收集你关心的安全信息源，然后生成一份按主题过滤的日报。

它支持两种使用方式：

- Claude Code
- Codex

支持的源类型包括：

- `github_user`
- `github_feed`
- `web`

其中：

- `github_feed` 是认证后的 GitHub Home Feed，通常使用 `fetch.handle = "@authenticated"`
- 不支持 RSS / Atom feed；如果你提供的是 feed URL，请改用站点主页或栏目页，并配置成 `web`

## 你会得到什么

第一次运行后，仓库里会出现这些文件：

- `planning/sources.toml`：你订阅的信息源
- `planning/topics.md`：你关心和忽略的主题
- `planning/report-style.md`：报告风格
- `data/runs/YYYY-MM-DD/report.md`：当天的最终报告

## 快速开始

先准备 Python 3.11+，然后在仓库根目录里选择一种方式使用。

### Claude Code

推荐方式：

```bash
claude --plugin-dir /absolute/path/to/daily-security-report
```

如果你更想走独立安装：

```bash
./scripts/claude_install.sh
```

安装后可用：

```text
/daily-security-report:daily-security-digest
```

说明：

- 不要同时使用 Claude plugin 和 `scripts/claude_install.sh`
- `scripts/claude_install.sh --config-only` 只会写 workspace 配置

### Codex

运行：

```bash
./scripts/codex_install.sh
```

这个脚本会安装两层内容：

- skills 容器目录到 `~/.agents/skills/daily-security-report`
- subagents 到 `~/.codex/agents/`

## 第一次运行时会发生什么

工作流大致分成 6 步：

1. 创建 `planning/` 下的配置模板
2. 让你提供 sources
3. 让你提供 topics
4. 抓取 GitHub 脚本源内容，并用 `web-source-collector` 并行采集 web sources
5. 按 topic 过滤并总结
6. 写出最终 `report.md`

当前运行时保留三个平台 subagents：

- `web-source-collector`
- `item-filter`
- `report-writer`

其中 web sources 由平台原生 collector 并行采集；每个 source 一个 collector，默认最多三跳、仅同域、每个 source 最多 20 篇 item。

## 配置 Sources

`planning/sources.toml` 里每个源是一个 `[[sources]]` 块：

```toml
[[sources]]
id = "my-security-site"
title = "My Security Site"
kind = "web"
enabled = true
fetch.url = "https://example.com/security"
```

支持的类型：

| Kind | 说明 | 必填 `fetch` |
|------|------|-------------|
| `github_user` | GitHub 用户公开事件流 | `handle` 或 `events_url` |
| `github_feed` | GitHub Home Feed | `handle`，通常是 `@authenticated` |
| `web` | 普通网页 | `url` |

`github_feed` 示例：

```toml
[[sources]]
id = "github-home"
title = "GitHub Home Feed"
kind = "github_feed"
enabled = true
fetch.handle = "@authenticated"
```

## 配置 Topics

`planning/topics.md` 里每个主题都要有：

- `### Care About`
- `### Usually Ignore`
- `### Reporting Angle`

不支持 `## All` 这种 catch-all 写法。

## 环境变量

| 变量 | 是否需要 | 用途 |
|------|----------|------|
| `GITHUB_TOKEN` | `github_feed` 必需；`github_user` 可选 | 用于访问 GitHub Home Feed，并提高 GitHub API 限额 |

GitHub 配置说明见：

- `docs/github-feed-setup.md`

## 常用命令

收集脚本源内容并准备 web source manifest：

```bash
python3 skills/daily-security-digest/scripts/collect_materials.py \
  --timezone Asia/Shanghai
```

解析一个 source 输入：

```bash
python3 skills/daily-security-digest/scripts/resolve_source.py \
  --input "https://example.com"
```

可选参数：

- `--date YYYY-MM-DD`
- `--days N`

## 输出位置

运行数据会写到当前仓库的 workspace 里，workspace 由：

- `skills/daily-security-digest/config.toml`

决定。

主要目录：

- `planning/`
- `data/runs/`

最终报告路径：

- `data/runs/YYYY-MM-DD/report.md`

## 测试

```bash
PYTHONPATH=skills/daily-security-digest/scripts \
python3 -m unittest discover -s tests -v
```

## 给维护者

普通使用者可以跳过这一节。

这个仓库只维护一份共享 skill 源目录：

- `skills/daily-security-digest/`

其中：

- `SKILL.md` 是 Claude Code 和 Codex 共用的 skill 说明
- `scripts/` 和 `templates/` 是共享 runtime
- `agents/` 和 `.codex/agents/` 分别存放 Claude / Codex 的平台专属 subagents

Codex 安装时会把整个仓库的 `skills/` 目录作为一个容器挂到用户目录下，因此后续如果仓库里新增新的独立 skill，不需要再修改 Codex 安装模型。
