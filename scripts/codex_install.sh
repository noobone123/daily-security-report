#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/codex_install.sh [--codex-dir PATH] [--copy]

Install the Daily Security Digest Codex skill and subagents into
Codex's personal standalone directories.

By default this writes into:
  ~/.agents/skills/daily-security-report
  ~/.codex/agents/web-source-collector.toml
  ~/.codex/agents/item-filter.toml
  ~/.codex/agents/report-writer.toml

It also writes:
  skills/daily-security-digest/config.toml
EOF
}

CODEX_DIR="${HOME}/.codex"
AGENTS_HOME="${HOME}/.agents"
COPY_MODE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --codex-dir)
      CODEX_DIR="${2:-}"
      shift 2
      ;;
    --copy)
      COPY_MODE=1
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

  echo "Symlink creation failed for ${target}. Rerun with --copy." >&2
  exit 1
}

cat > "${CONFIG_PATH}" <<EOF
workspace_root = "${REPO_ROOT}"
EOF

CODEX_DIR="$(mkdir -p "${CODEX_DIR}" && cd "${CODEX_DIR}" && pwd)"
AGENTS_HOME="$(mkdir -p "${AGENTS_HOME}" && cd "${AGENTS_HOME}" && pwd)"
AGENTS_DIR="${CODEX_DIR}/agents"
SKILLS_DIR="${AGENTS_HOME}/skills"
mkdir -p "${AGENTS_DIR}" "${SKILLS_DIR}"

install_item "${REPO_ROOT}/skills" "${SKILLS_DIR}/daily-security-report"

for agent in web-source-collector item-filter report-writer; do
  install_item "${REPO_ROOT}/.codex/agents/${agent}.toml" "${AGENTS_DIR}/${agent}.toml"
done

echo "Installed Daily Security Digest Codex skills container into ${SKILLS_DIR}/daily-security-report"
echo "Installed Daily Security Digest Codex subagents into ${AGENTS_DIR}"
echo "Wrote workspace config to ${CONFIG_PATH}"
