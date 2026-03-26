#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from skill_lib import resolve_source


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve a user-provided source input into a sources.toml entry.")
    parser.add_argument("--input", required=True, help="URL or GitHub username")
    parser.add_argument("--user-label", default="", help="Optional display label from the user")
    args = parser.parse_args(argv)
    try:
        payload = resolve_source(args.input, user_label=args.user_label)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=False))
        return 1
    print(json.dumps({"ok": True, "result": payload}, indent=2, sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
