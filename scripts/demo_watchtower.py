#!/usr/bin/env python3
"""Probe explicitly configured public endpoints and preserve a JSON report."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

USER_AGENT = "portfolio-ops-demo-watchtower/1.0"


def probe(url: str, attempts: int, timeout: float) -> dict[str, object]:
    errors: list[str] = []
    started = time.monotonic()
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = response.status
                response.read(1024)
            return {
                "url": url,
                "ok": 200 <= status < 400,
                "status": status,
                "attempts": attempt,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
            }
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            errors.append(str(error))
            if attempt < attempts:
                time.sleep(min(attempt, 2))
    return {
        "url": url,
        "ok": False,
        "status": None,
        "attempts": attempts,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
        "error": errors[-1] if errors else "unknown failure",
    }


def parse_urls(raw: str) -> list[str]:
    return [line.strip() for line in raw.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("urls", nargs="*")
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--report", type=Path, default=Path("demo-health.json"))
    args = parser.parse_args(argv)

    urls = args.urls or parse_urls(os.environ.get("WATCH_URLS", ""))
    if not urls:
        parser.error("provide URLs as arguments or WATCH_URLS")
    results = [probe(url, args.attempts, args.timeout) for url in urls]
    report = {
        "schema": 1,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "ok": all(item["ok"] for item in results),
        "results": results,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    for item in results:
        label = "ok" if item["ok"] else "failed"
        print(f"{label}: {item['url']} status={item['status']} attempts={item['attempts']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
