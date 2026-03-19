#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/claude_install.sh [--claude-dir PATH] [--copy] [--config-only]

Install the canonical skill and subagent definitions from this repository into
Claude Code's personal standalone directories.

By default this writes into:
  ~/.claude/skills/daily-security-digest
  ~/.claude/agents/*.md

It also writes:
  skills/daily-security-digest/config.toml
EOF
}

CLAUDE_DIR="${HOME}/.claude"
COPY_MODE=0
CONFIG_ONLY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --claude-dir)
      CLAUDE_DIR="${2:-}"
      shift 2
      ;;
    --copy)
      COPY_MODE=1
      shift
      ;;
    --config-only)
      CONFIG_ONLY=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG_PATH="${REPO_ROOT}/skills/daily-security-digest/config.toml"
mkdir -p "$(dirname "${CONFIG_PATH}")"

resolve_path() {
  local path="$1"
  local dir base
  dir="$(cd "$(dirname "${path}")" && pwd)"
  base="$(basename "${path}")"
  printf '%s/%s\n' "${dir}" "${base}"
}

copy_item() {
  local source="$1"
  local target="$2"
  if [[ -d "${source}" ]]; then
    cp -R "${source}" "${target}"
  else
    cp "${source}" "${target}"
  fi
}

install_item() {
  local source="$1"
  local target="$2"
  local source_abs target_current

  source_abs="$(resolve_path "${source}")"
  if [[ -L "${target}" ]]; then
    target_current="$(readlink "${target}")"
    if [[ "${target_current}" == "${source_abs}" ]]; then
      return 0
    fi
    echo "Refusing to replace existing symlink at ${target}" >&2
    exit 1
  fi

  if [[ -e "${target}" ]]; then
    echo "Refusing to overwrite existing path at ${target}" >&2
    exit 1
  fi

  if [[ "${COPY_MODE}" -eq 1 ]]; then
    copy_item "${source_abs}" "${target}"
    return 0
  fi

  if ln -s "${source_abs}" "${target}" 2>/dev/null; then
    return 0
  fi

  echo "Symlink creation failed for ${target}. Use plugin loading or rerun with --copy." >&2
  exit 1
}

write_config() {
  cat > "${CONFIG_PATH}" <<EOF
workspace_root = "${REPO_ROOT}"
EOF
}

write_config

if [[ "${CONFIG_ONLY}" -eq 1 ]]; then
  echo "Wrote workspace config to ${CONFIG_PATH}"
  exit 0
fi

CLAUDE_DIR="$(mkdir -p "${CLAUDE_DIR}" && cd "${CLAUDE_DIR}" && pwd)"
SKILLS_DIR="${CLAUDE_DIR}/skills"
AGENTS_DIR="${CLAUDE_DIR}/agents"

mkdir -p "${SKILLS_DIR}" "${AGENTS_DIR}"

install_item "${REPO_ROOT}/skills/daily-security-digest" "${SKILLS_DIR}/daily-security-digest"

for agent in source-resolver web-source-collector item-filter report-writer; do
  install_item "${REPO_ROOT}/agents/${agent}.md" "${AGENTS_DIR}/${agent}.md"
done

echo "Installed Daily Security Digest into ${CLAUDE_DIR}"
echo "Wrote workspace config to ${CONFIG_PATH}"
