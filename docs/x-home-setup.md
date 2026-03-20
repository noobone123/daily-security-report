# X 首页流配置

## 结论

本项目目前只需要一个环境变量：`X_USER_ACCESS_TOKEN`

它用于调用 X 官方 Home Timeline API。
它不是 `For You`，而是官方首页流。

## 你只需要什么

- 一个可用的 `X_USER_ACCESS_TOKEN`
- `planning/sources.toml` 里有一条 `x_home`

本项目不会把这个 token 写进 `config.toml` 或 `sources.toml`。

## 3 步配置

### 1. 获取 token

去 X Developer Portal。
找到你 app 的 User Access Token，复制出来。

官方文档：

- X API introduction: https://docs.x.com/x-api/introduction
- Home timeline quickstart: https://docs.x.com/x-api/posts/timelines/quickstart/reverse-chron-quickstart
- Authenticated user / users API: https://docs.x.com/x-api/users/get-user-by-username

### 2. 本地配置

当前 shell：

```bash
export X_USER_ACCESS_TOKEN='paste-token-here'
```

长期生效：

```bash
echo "export X_USER_ACCESS_TOKEN='paste-token-here'" >> ~/.zshrc
source ~/.zshrc
```

### 3. 配置 source

在 `planning/sources.toml` 里加入：

```toml
[[sources]]
id = "x-home"
title = "X Home Timeline"
kind = "x_home"
enabled = true
notes = "Official X home timeline."
```

`x_home` 不需要 `fetch.*` 字段。

## 如何验证

先检查环境变量：

```bash
if [ -n "${X_USER_ACCESS_TOKEN:-}" ]; then
  echo "X_USER_ACCESS_TOKEN is set"
else
  echo "X_USER_ACCESS_TOKEN is missing"
fi
```

再运行采集：

```bash
python3 skills/daily-security-digest/scripts/collect_materials.py --timezone Asia/Shanghai
```

如果配置正确：

- 不会出现 `x_home requires X_USER_ACCESS_TOKEN`
- 脚本会先请求 `/2/users/me`
- 再请求 `reverse_chronological home timeline`

## 常见报错

### 1. `x_home requires X_USER_ACCESS_TOKEN`

当前 shell 看不到 token。
重新 `export`，或者确认你已经 `source ~/.zshrc`。

### 2. 401 / 403

通常是 token 无效、app 没开通、或者账号权限不够。
先去 X Developer Portal 检查 app 和 token 状态。

### 3. 为什么不是 `For You`

本项目当前只支持官方 Home Timeline。
不支持网页里的 `For You` 推荐流。
