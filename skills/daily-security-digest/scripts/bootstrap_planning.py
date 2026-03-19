#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from skill_lib import ValidationError, bootstrap_planning


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bootstrap planning files from bundled skill templates.")
    parser.add_argument("--workspace", default=".", help="Workspace root")
    parser.add_argument(
        "--templates",
        default=str(SCRIPT_DIR.parent / "templates"),
        help="Directory containing planning templates",
    )
    args = parser.parse_args(argv)

    try:
        payload = bootstrap_planning(Path(args.workspace), Path(args.templates))
    except ValidationError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=False))
        return 1

    print(json.dumps({"ok": True, **payload}, indent=2, sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
