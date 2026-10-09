"""Refreshes engine_fixtures against a live dev stack (ticket #8).

Usage (operator, needs the stack up):
    python apps/gateway/tests/record.py --record [--query "..." --url "..."]

Without --record it only prints what would be fetched. Recorded payloads are
written with manifest mode "recorded-v1" (date + engine images). Review the
diff before committing: upstream engine schema changes show up here first —
that is the point.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "engine_fixtures"
SEED_QUERY = "scraper swarm"
SEED_URL = "https://example.com/"


def _manifest(mode: str) -> dict:
    try:
        current = json.loads((FIXTURES / "MANIFEST.json").read_text())
        engines = current.get("engines", {})
    except Exception:
        engines = {}
    return {
        "mode": mode,
        "generated_at": datetime.now(UTC).isoformat(),
        "engines": engines,
        "note": (
            "Recorded live via record.py --record. Refresh cadence: whenever "
            "an engine image tag changes, or the replay tests fail after a "
            "record refresh (upstream schema drift)."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--query", default=SEED_QUERY)
    ap.add_argument("--url", default=SEED_URL)
    args = ap.parse_args(argv)
    searxng = os.environ.get("SEARXNG_URL", "http://searxng:8080")
    crawl4ai = os.environ.get("CRAWL4AI_URL", "http://crawl4ai:11235")
    plan = {
        "searxng_basic.json": f"GET {searxng}/search?q={args.query}&format=json",
        "crawl4ai_md.json": f"POST {crawl4ai}/md {args.url}",
        "crawl4ai_crawl.json": f"POST {crawl4ai}/crawl {args.url}",
    }
    if not args.record:
        print("Would record (pass --record against a live dev stack):")
        for name, call in plan.items():
            print(f"  {name}: {call}")
        return 0
    import httpx

    with httpx.Client(timeout=60.0) as client:
        sear = client.get(f"{searxng}/search", params={"q": args.query, "format": "json"})
        sear.raise_for_status()
        (FIXTURES / "searxng_basic.json").write_text(json.dumps(sear.json(), indent=2) + "\n")
        md = client.post(f"{crawl4ai}/md", json={"url": args.url})
        if md.status_code == 200:
            (FIXTURES / "crawl4ai_md.json").write_text(json.dumps(md.json(), indent=2) + "\n")
        crawl = client.post(f"{crawl4ai}/crawl", json={"urls": [args.url]})
        crawl.raise_for_status()
        (FIXTURES / "crawl4ai_crawl.json").write_text(json.dumps(crawl.json(), indent=2) + "\n")
    (FIXTURES / "MANIFEST.json").write_text(json.dumps(_manifest("recorded-v1"), indent=2) + "\n")
    print("Recorded engine payloads (mode recorded-v1). Review the diff, then commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
