#!/usr/bin/env python3
"""Track verified GitHub achievements without inferring undocumented awards."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

PATTERN = re.compile(r'alt=["\']Achievement:\s*([^"\']+)["\']', re.IGNORECASE)


def fetch(user: str) -> str:
    url = f"https://github.com/{user}?tab=achievements"
    request = urllib.request.Request(url, headers={"User-Agent": "portfolio-ops-achievement-scout/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def parse(page: str) -> list[str]:
    return sorted(set(match.strip() for match in PATTERN.findall(page)))


def read_previous(path: Path) -> list[str]:
    try:
        value = json.loads(path.read_text())
        return sorted(set(value.get("achievements", [])))
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        return []


def write_output(name: str, value: str) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if not output:
        return
    with open(output, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", default="asp53826")
    parser.add_argument("--state", type=Path, default=Path("state/achievements.json"))
    parser.add_argument("--page", type=Path, help="offline HTML fixture")
    args = parser.parse_args(argv)

    page = args.page.read_text(errors="replace") if args.page else fetch(args.user)
    achievements = parse(page)
    if not achievements:
        raise SystemExit("achievement page returned no visible achievements; state left untouched")
    previous = read_previous(args.state)
    added = sorted(set(achievements) - set(previous))
    removed = sorted(set(previous) - set(achievements))
    changed = achievements != previous
    state = {
        "schema": 1,
        "user": args.user,
        "source": f"https://github.com/{args.user}?tab=achievements",
        "achievements": achievements,
    }
    args.state.parent.mkdir(parents=True, exist_ok=True)
    args.state.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    write_output("changed", str(changed).lower())
    write_output("added", ", ".join(added))
    write_output("removed", ", ".join(removed))
    print(f"verified achievements: {', '.join(achievements)}")
    if changed:
        print(f"added: {', '.join(added) or 'none'}; removed: {', '.join(removed) or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
