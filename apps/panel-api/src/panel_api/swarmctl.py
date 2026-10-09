"""swarmctl: host-operator CLI for service-account keys (ticket #7).

Run on the host (e.g. ``docker compose exec panel-api swarmctl keys create
...``). Host shell access is the authorization, consistent with the bootstrap
model — there is deliberately no network path here. Every mutation writes an
audit row (actor ``swarmctl``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from panel_api.audit import AuditLogger
from panel_api.db import Database
from panel_api.keys import (
    KeyExistsError,
    expiry_iso,
    find_key,
    mint_key,
    rotate_key,
)

DEFAULT_DB_PATH = "/var/lib/scraper-swarm/panel.db"

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhd])?\s*$", re.IGNORECASE)
_DURATION_MULT = {"s": 1 / 3600, "m": 1 / 60, "h": 1, "d": 24}


def parse_duration_hours(text: str) -> float | None:
    """Parses '90d'/'24h'/'30m'/'60s'/bare-hours into hours; 'never' -> None."""
    cleaned = text.strip().lower()
    if cleaned == "never":
        return None
    m = _DURATION_RE.match(cleaned)
    if not m:
        raise ValueError(f"Bad duration '{text}': use like 90d, 24h, 720, or never")
    amount = int(m.group(1))
    unit = (m.group(2) or "h").lower()
    return amount * _DURATION_MULT[unit]


def _db_path(args) -> Path:
    return Path(args.db or os.environ.get("SWARM_DB_PATH", DEFAULT_DB_PATH))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="swarmctl", description="Scraper Swarm operator CLI")
    p.add_argument("--db", default=None, help="panel DB path (default $SWARM_DB_PATH)")
    sub = p.add_subparsers(dest="group", required=True)
    keys = sub.add_parser("keys", help="service-account keys")
    ksub = keys.add_subparsers(dest="cmd", required=True)

    c = ksub.add_parser("create", help="mint a service-account key (prints raw key once)")
    c.add_argument("--name", required=True)
    c.add_argument("--scopes", default="search,scrape")
    c.add_argument("--rpm", type=int, default=60)
    c.add_argument("--expires", default="720h")
    c.add_argument("--allow-never-expire", action="store_true")
    c.add_argument(
        "--audit-mode",
        default="default",
        help="audit query privacy: default (global), verbatim, hashed, redacted",
    )

    r = ksub.add_parser("rotate", help="rotate a key (old stays valid for --grace)")
    r.add_argument("ref", help="key id or name")
    r.add_argument("--grace", default="24h")
    r.add_argument("--expires", default="720h")
    r.add_argument("--allow-never-expire", action="store_true")

    ksub.add_parser("list", help="list keys (prefixes only, never secrets)")
    d = ksub.add_parser("revoke", help="revoke (delete) a key")
    d.add_argument("ref", help="key id or name")

    audit = sub.add_parser("audit", help="audit log maintenance")
    asub = audit.add_subparsers(dest="cmd", required=True)
    pr = asub.add_parser("prune", help="delete rows older than retention, keep chain verifiable")
    pr.add_argument(
        "--retention-days",
        default=None,
        help="override SWARM_AUDIT_RETENTION_DAYS (required when unset)",
    )
    return p


async def _cmd_create(db: Database, args) -> int:
    hours = parse_duration_hours(args.expires)
    if hours is None and not args.allow_never_expire:
        print(
            "error: --expires never creates a never-expiring key;"
            " pass --allow-never-expire to acknowledge",
            file=sys.stderr,
        )
        return 1
    scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]
    try:
        key = await mint_key(
            db.conn,
            name=args.name,
            scopes=scopes,
            rate_limit_rpm=args.rpm,
            expires_at=expiry_iso(None if hours is None else int(hours)),
            audit_query_mode=args.audit_mode,
        )
    except KeyExistsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    await AuditLogger(db.conn).log(
        actor="swarmctl",
        action="create_agent_key",
        target=key["id"],
        details={
            "name": key["name"],
            "scopes": key["scopes"],
            "key_prefix": key["key_prefix"],
            "audit_query_mode": key["audit_query_mode"] or "default",
        },
    )
    print(f"created key '{key['name']}' (id {key['id']})")
    print(f"raw_key: {key['raw_key']}")
    print("Store the raw key now — it is shown once and never again.")
    print(
        f"scopes: {','.join(key['scopes'])} rpm: {key['rate_limit_rpm']}"
        f" expires_at: {key['expires_at']}"
    )
    return 0


async def _cmd_rotate(db: Database, args) -> int:
    try:
        grace_hours = parse_duration_hours(args.grace)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if grace_hours is None:
        print("error: --grace must be a duration, not never", file=sys.stderr)
        return 1
    hours = parse_duration_hours(args.expires)
    if hours is None and not args.allow_never_expire:
        print("error: --expires never requires --allow-never-expire", file=sys.stderr)
        return 1
    try:
        result = await rotate_key(
            db.conn,
            ref=args.ref,
            grace_hours=grace_hours,
            expires_in_hours=None if hours is None else int(hours),
        )
    except (KeyError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    new = result["new"]
    old = result["old"]
    await AuditLogger(db.conn).log(
        actor="swarmctl",
        action="rotate_agent_key",
        target=new["id"],
        details={
            "name": new["name"],
            "retired_id": old["id"],
            "grace_expires_at": old["expires_at"],
        },
    )
    print(f"rotated '{new['name']}': old id {old['id']} valid until {old['expires_at']}")
    print(f"raw_key: {new['raw_key']}")
    print("Store the raw key now — it is shown once and never again.")
    return 0


async def _cmd_list(db: Database, args) -> int:
    async with db.conn.execute(
        "SELECT id, name, key_prefix, scopes, rate_limit_rpm, created_at,"
        " expires_at, last_used_at, audit_query_mode FROM agent_keys ORDER BY created_at DESC"
    ) as cur:
        rows = await cur.fetchall()
    print(
        json.dumps(
            [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "key_prefix": r["key_prefix"],
                    "scopes": json.loads(r["scopes"]),
                    "rate_limit_rpm": r["rate_limit_rpm"],
                    "created_at": r["created_at"],
                    "expires_at": r["expires_at"],
                    "last_used_at": r["last_used_at"],
                    "audit_query_mode": r["audit_query_mode"] or "default",
                }
                for r in rows
            ],
            indent=2,
        )
    )
    return 0


async def _cmd_revoke(db: Database, args) -> int:
    row = await find_key(db.conn, args.ref)
    if row is None:
        print(f"error: key '{args.ref}' not found", file=sys.stderr)
        return 1
    await db.conn.execute("DELETE FROM agent_keys WHERE id = ?", (row["id"],))
    await db.conn.commit()
    await AuditLogger(db.conn).log(actor="swarmctl", action="revoke_agent_key", target=row["id"])
    print(f"revoked key '{row['name']}' (id {row['id']})")
    return 0


async def _cmd_audit_prune(db: Database, args) -> int:
    """Deletes audit rows older than retention, keeping a checkpoint row so
    verify_chain still passes (ticket #10)."""
    raw_days = args.retention_days
    if raw_days is None:
        raw_days = os.environ.get("SWARM_AUDIT_RETENTION_DAYS", "")
    try:
        days = float(str(raw_days).strip())
    except ValueError:
        print(
            "error: retention unknown: pass --retention-days or set SWARM_AUDIT_RETENTION_DAYS",
            file=sys.stderr,
        )
        return 1
    if days <= 0:
        print("error: retention must be positive days (0 disables pruning)", file=sys.stderr)
        return 1
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    pruned = await AuditLogger(db.conn).prune_older_than(cutoff)
    await AuditLogger(db.conn).log(
        actor="swarmctl",
        action="prune_audit",
        details={"pruned": pruned, "cutoff": cutoff},
    )
    print(f"pruned {pruned} audit rows older than {cutoff} (checkpoint kept)")
    return 0


async def amain(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db = Database(_db_path(args))
    await db.connect()
    try:
        if args.group == "keys":
            if args.cmd == "create":
                return await _cmd_create(db, args)
            if args.cmd == "rotate":
                return await _cmd_rotate(db, args)
            if args.cmd == "list":
                return await _cmd_list(db, args)
            if args.cmd == "revoke":
                return await _cmd_revoke(db, args)
        elif args.group == "audit":
            if args.cmd == "prune":
                return await _cmd_audit_prune(db, args)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        await db.close()
    print("error: unknown command", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    """Sync entry point for the `swarmctl` console script."""
    return asyncio.run(amain(argv))


if __name__ == "__main__":
    raise SystemExit(main())
