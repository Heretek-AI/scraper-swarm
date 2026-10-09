# Robots.txt and User-Agent policy (gateway contract v1, ticket #6)

## Status

Decision recorded; enforcement is a follow-up (no code change in #6).

## Current behaviour (verified at #6)

- The gateway performs no `robots.txt` check. `fetch_page` delegates the
  target URL to Crawl4AI, `stealth_scrape` to Scrapling; both engines send
  their own native user-agents. There is no gateway-level UA override.
- Every fetch path runs the SSRF pre-check (`deny_reason_for_url`) and all
  engine HTTP traverses the Smokescreen egress proxy, but neither layer
  consults `robots.txt`.

## Decision

- **v1: no gateway-level `robots.txt` enforcement.** The gateway exposes
  single-URL, user-directed fetch (`fetch_page`, `stealth_scrape`), not bulk
  crawling. Politeness for bulk/recurring collection stays the operator's
  responsibility (engine configuration, scheduling).
- **User-agent: engines keep their native UAs in v1.** A fixed identifying
  UA (`ScraperSwarmGateway/<version> (+contact)`) is desirable so site
  operators can identify and rate-limit us, but overriding per-engine UAs
  (in particular Scrapling's stealth camouflage, where the UA *is* the
  feature) needs per-engine design.
- **Follow-up:** file a ticket to (a) evaluate Crawl4AI/Scrapling native
  `robots.txt` support and enable it where it is a flag, and (b) design the
  identifying-UA override per engine. Revisit when an automated client
  starts high-volume fetching.

## Why not enforce now

Fetching `robots.txt` per URL adds a second fetch (with its own SSRF,
caching, and timeout questions) to every call; getting that wrong weakens
the security posture #6 just hardened. A half-enforced robots check is
worse than an honest documented gap.
