"""Operator commands. Bootstrap the first manager:

    docker compose exec api python -m app.cli create-user --email m@agency.il \
        --name "Manager" --role manager
"""

import argparse
import getpass
import sys

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.audit import chain
from app.auth import service as auth
from app.auth.passwords import check_policy
from app.capabilities import ROLES
from app.config import get_settings
from app.db import make_engine
from app.models import User


def create_user(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="create-user")
    ap.add_argument("--email", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--role", action="append", required=True, choices=ROLES)
    ap.add_argument("--sso", action="store_true", help="user signs in through SSO only")
    ap.add_argument("--password", help="omit to be prompted (preferred)")
    args = ap.parse_args(argv)

    password = None
    if not args.sso:
        password = args.password or getpass.getpass("Password: ")
        min_len = get_settings().password_min_length
        if (reason := check_policy(password, args.email, min_len)) is not None:
            print(f"password rejected: {reason}", file=sys.stderr)
            return 2
    maker = sessionmaker(make_engine(get_settings()))
    with maker() as db:
        email = args.email.strip().lower()
        existing = db.scalar(select(User).where(User.email == email))
        mode = "sso" if args.sso else "local"
        if existing is None:
            user, extra = auth.create_user(db, args.email, args.name, args.role, password, mode)
            action = "user.create"
        else:  # onboarding again replaces the user: new password, authenticator, codes, roles
            extra = auth.overwrite_user(db, existing, args.name, args.role, password, mode)
            user, action = existing, "user.overwrite"
        chain.append(db, "operator", action, f"user:{user.id}", {"roles": args.role, "via": "cli"})
        db.commit()
    verb = "created" if action == "user.create" else "overwrote existing user"
    print(f"{verb} {args.email} ({', '.join(args.role)})")
    if "totp_uri" in extra:
        print(f"TOTP URI (add to an authenticator app): {extra['totp_uri']}")
        print("Recovery codes (shown once):", *extra["recovery_codes"], sep="\n  ")  # type: ignore[misc]
    return 0


def reconcile_blobs() -> int:
    """Report objects without rows and rows without objects. Never deletes anything."""
    from app.documents.reconcile import reconcile
    from app.main import make_blob_store

    settings = get_settings()
    maker = sessionmaker(make_engine(settings))
    with maker() as db:
        rep = reconcile(db, make_blob_store(settings), settings.s3_bucket_originals)
    print(f"orphans (object, no row): {len(rep.orphans)}")
    print(*rep.orphans, sep="\n  ")
    print(f"missing (row, no object): {len(rep.missing)}")
    print(*rep.missing, sep="\n  ")
    return 0 if not rep.missing else 1


def seed_processes() -> int:
    """Load the process definitions that are not in the database yet (idempotent)."""
    from app.processes.service import seed_definitions

    maker = sessionmaker(make_engine(get_settings()))
    with maker() as db:
        added = seed_definitions(db)
        db.commit()
    print(f"process definitions added: {added}")
    return 0


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "create-user":
        return create_user(sys.argv[2:])
    if cmd == "reconcile":
        return reconcile_blobs()
    if cmd == "seed-processes":
        return seed_processes()
    print("usage: python -m app.cli create-user|reconcile|seed-processes ...", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
