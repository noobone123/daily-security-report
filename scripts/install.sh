#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/install.sh --mode project|global [--target PATH] [--copy]

Installs the canonical skill and subagent definitions from this repository into
Claude's standalone .claude directories.
EOF
}

MODE=""
TARGET=""
COPY_MODE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      MODE="${2:-}"
      shift 2
      ;;
    --target)
      TARGET="${2:-}"
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

if [[ -z "${MODE}" ]]; then
  echo "--mode is required" >&2
  usage >&2
  exit 1
fi

case "${MODE}" in
  project)
    if [[ -z "${TARGET}" ]]; then
      TARGET="."
    fi
    ;;
  global)
    if [[ -z "${TARGET}" ]]; then
      TARGET="${HOME}"
    fi
    ;;
  *)
    echo "Unsupported mode: ${MODE}" >&2
    exit 1
    ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TARGET_ROOT="$(cd "${TARGET}" && pwd)"
CLAUDE_DIR="${TARGET_ROOT}/.claude"
SKILLS_DIR="${CLAUDE_DIR}/skills"
AGENTS_DIR="${CLAUDE_DIR}/agents"

mkdir -p "${SKILLS_DIR}" "${AGENTS_DIR}"

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

install_item "${REPO_ROOT}/skills/daily-security-digest" "${SKILLS_DIR}/daily-security-digest"

for agent in source-resolver web-source-collector item-filter report-writer; do
  install_item "${REPO_ROOT}/agents/${agent}.md" "${AGENTS_DIR}/${agent}.md"
done

echo "Installed Daily Security Digest into ${CLAUDE_DIR}"
