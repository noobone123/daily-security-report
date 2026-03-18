#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import date as _date
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from skill_lib import ValidationError, run_collection


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch enabled security sources into local Markdown materials.")
    parser.add_argument("--workspace", default=".", help="Workspace root")
    parser.add_argument("--date", default=str(_date.today()), help="Collection date YYYY-MM-DD (default: today)")
    parser.add_argument("--timezone", default="Asia/Shanghai", help="IANA timezone name")
    parser.add_argument("--days", type=int, default=None, help="Days to look back (default: auto-continue from last run, or 3 if first run)")
    args = parser.parse_args(argv)
    try:
        manifest = run_collection(Path(args.workspace), date_slug=args.date, timezone=args.timezone, days=args.days)
    except (ValidationError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=False))
        return 1
    print(json.dumps({"ok": True, "manifest": manifest}, indent=2, sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
