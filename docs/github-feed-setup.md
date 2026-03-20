# GitHub Feed Setup

This project's `github_feed` source reads the authenticated user's GitHub home feed through the GitHub REST API. It does not scrape the GitHub web UI.

## What You Need

- A GitHub personal access token exposed as the `GITHUB_TOKEN` environment variable
- A `github_feed` source in `planning/sources.toml`

This project does **not** store the token in `config.toml` or `sources.toml`.

## Recommended Token Type

Use a **fine-grained personal access token**.

GitHub's docs currently state that the two endpoints used by this project support fine-grained tokens:

- `GET /user` ("Get the authenticated user")
- `GET /users/{username}/received_events` ("List events received by the authenticated user")

For fine-grained tokens, GitHub documents these endpoints as not requiring extra permissions. In practice, organization policy, approval requirements, or SSO rules can still affect what the token can access.

Official docs:

- Managing personal access tokens: https://docs.github.com/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens
- Events API: https://docs.github.com/en/rest/activity/events
- Users API: https://docs.github.com/en/rest/users/users
- Fine-grained token permissions reference: https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens
- Token security guidance: https://docs.github.com/en/rest/overview/keeping-your-api-credentials-secure
- Organization token policy / approval: https://docs.github.com/en/organizations/managing-programmatic-access-to-your-organization/setting-a-personal-access-token-policy-for-your-organization

## How To Create The Token

On GitHub:

1. Click your avatar in the upper-right corner.
2. Open `Settings`.
3. Open `Developer settings`.
4. Open `Personal access tokens`.
5. Choose `Fine-grained tokens`.
6. Click `Generate new token`.
7. Set a short expiration date.
8. Select the resource owner that matches the account whose home feed you want to read.
9. Keep permissions minimal.
10. Generate the token and copy it once.

Notes:

- If GitHub or your organization asks for approval, complete that approval flow.
- If you belong to organizations with special token policies, the token may need extra approval before private org activity appears.
- GitHub notes that the events API is not real-time; latency can range from seconds to hours.

## How To Expose `GITHUB_TOKEN`

For the current terminal session:

```bash
export GITHUB_TOKEN='paste-your-token-here'
```

To make it available in future `zsh` sessions:

```bash
echo "export GITHUB_TOKEN='paste-your-token-here'" >> ~/.zshrc
source ~/.zshrc
```

If you prefer not to put the token directly in shell history, open an editor and add the line manually to `~/.zshrc`.

## Configure `sources.toml`

Add this block to `planning/sources.toml`:

```toml
[[sources]]
id = "github-home"
title = "GitHub Home Feed"
kind = "github_feed"
enabled = true
fetch.handle = "@authenticated"
notes = "Authenticated GitHub home feed."
```

## Verify The Setup

Check whether the current shell can see the token:

```bash
if [ -n "${GITHUB_TOKEN:-}" ]; then echo "GITHUB_TOKEN is set"; else echo "GITHUB_TOKEN is missing"; fi
```

Run a collection:

```bash
python3 skills/daily-security-digest/scripts/collect_materials.py --timezone Asia/Shanghai
```

If setup is correct, `manifest.json` should not report a `github_feed requires GITHUB_TOKEN` failure.

## Troubleshooting

If you still do not see GitHub home feed items:

- Confirm the token belongs to the same GitHub account whose home feed you expect.
- Confirm the token was copied correctly and is visible in the shell running the script.
- If your org enforces token approval, approve the token first.
- If you used a classic token in an org with stricter policy, switch to a fine-grained token.
- Remember that GitHub's events API is delayed and may not show activity immediately.
