"""Mint / list / revoke third-party API client keys. Run in the backend container.

  # create — prints the raw key ONCE (store it securely):
  python scripts/mint_api_key.py create "AuditAIQ (prod)" --scopes extract,classify,audit:match --rpm 120
  python scripts/mint_api_key.py list
  python scripts/mint_api_key.py revoke <pk>
  python scripts/mint_api_key.py grant-scope <pk> audit:ingest
  python scripts/mint_api_key.py grant-group <pk> <group_id>

Only the SHA-256 hash is stored; the raw key cannot be recovered later.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from sqlalchemy import select

from app import api_keys
from app.config import get_settings
from app.db import SessionLocal, set_current_tenant
from app.orm import ApiClient


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("name")
    c.add_argument("--scopes", default="extract")
    c.add_argument("--env", default="live", choices=["live", "test"])
    c.add_argument("--rpm", type=int, default=120)
    sub.add_parser("list")
    rv = sub.add_parser("revoke")
    rv.add_argument("pk", type=int)
    gr = sub.add_parser("grant-group")     # grant a key access to a group (shared folder)
    gr.add_argument("pk", type=int)
    gr.add_argument("group_id", type=int)
    ug = sub.add_parser("ungrant-group")
    ug.add_argument("pk", type=int)
    ug.add_argument("group_id", type=int)
    gsc = sub.add_parser("grant-scope")    # add a scope to a key (e.g. audit:ingest)
    gsc.add_argument("pk", type=int)
    gsc.add_argument("scope")
    rsc = sub.add_parser("revoke-scope")
    rsc.add_argument("pk", type=int)
    rsc.add_argument("scope")
    args = ap.parse_args()

    tid = get_settings().tenant_id
    set_current_tenant(tid)
    db = SessionLocal()

    if args.cmd == "create":
        scopes = [s.strip() for s in args.scopes.split(",") if s.strip()] or ["extract"]
        raw = api_keys.generate_key(args.env)
        row = ApiClient(
            tenant_id=tid, name=args.name, key_prefix=api_keys.key_prefix(raw),
            key_hash=api_keys.hash_key(raw), env=api_keys.parse_env(raw),
            scopes=scopes, rate_limit_rpm=args.rpm, created_by="mint_api_key.py",
        )
        db.add(row)
        db.commit()
        print(f"created client pk={row.pk} name={row.name!r} env={row.env} scopes={scopes} rpm={args.rpm}")
        print("API KEY (shown ONCE — store securely):")
        print(f"  {raw}")
    elif args.cmd == "list":
        for r in db.scalars(select(ApiClient).order_by(ApiClient.pk)):
            state = "revoked" if r.revoked_at else "active"
            print(f"  pk={r.pk} {state} {r.key_prefix} {r.name!r} scopes={r.scopes} "
                  f"groups={r.allowed_group_ids or []} rpm={r.rate_limit_rpm} last_used={r.last_used_at}")
    elif args.cmd == "revoke":
        r = db.get(ApiClient, args.pk)
        if r is None:
            print(f"no client pk={args.pk}")
            return
        r.revoked_at = datetime.now(timezone.utc)
        db.commit()
        print(f"revoked client pk={r.pk} ({r.name!r})")
    elif args.cmd in ("grant-group", "ungrant-group"):
        r = db.get(ApiClient, args.pk)
        if r is None:
            print(f"no client pk={args.pk}")
            return
        groups = set(r.allowed_group_ids or [])
        if args.cmd == "grant-group":
            groups.add(args.group_id)
        else:
            groups.discard(args.group_id)
        r.allowed_group_ids = sorted(groups)
        db.commit()
        print(f"client pk={r.pk} ({r.name!r}) allowed_group_ids={r.allowed_group_ids}")
    elif args.cmd in ("grant-scope", "revoke-scope"):
        r = db.get(ApiClient, args.pk)
        if r is None:
            print(f"no client pk={args.pk}")
            return
        scopes = set(r.scopes or [])
        if args.cmd == "grant-scope":
            scopes.add(args.scope)
        else:
            scopes.discard(args.scope)
        r.scopes = sorted(scopes)
        db.commit()
        print(f"client pk={r.pk} ({r.name!r}) scopes={r.scopes}")


if __name__ == "__main__":
    main()
