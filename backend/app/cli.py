"""Operator commands. Bootstrap the first manager:

    docker compose exec api python -m app.cli create-user --email m@agency.il \
        --name "Manager" --role manager
"""

import argparse
import getpass
import sys

from sqlalchemy.orm import sessionmaker

from app.audit import chain
from app.auth import service as auth
from app.auth.passwords import check_policy
from app.capabilities import ROLES
from app.config import get_settings
from app.db import make_engine


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
        if (reason := check_policy(password, args.email)) is not None:
            print(f"password rejected: {reason}", file=sys.stderr)
            return 2
    maker = sessionmaker(make_engine(get_settings()))
    with maker() as db:
        user, extra = auth.create_user(
            db, args.email, args.name, args.role, password, "sso" if args.sso else "local"
        )
        chain.append(
            db, "operator", "user.create", f"user:{user.id}", {"roles": args.role, "via": "cli"}
        )
        db.commit()
    print(f"created {args.email} ({', '.join(args.role)})")
    if "totp_uri" in extra:
        print(f"TOTP URI (add to an authenticator app): {extra['totp_uri']}")
        print("Recovery codes (shown once):", *extra["recovery_codes"], sep="\n  ")  # type: ignore[misc]
    return 0


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] != "create-user":
        print(
            "usage: python -m app.cli create-user --email ... --name ... --role ...",
            file=sys.stderr,
        )
        return 1
    return create_user(sys.argv[2:])


if __name__ == "__main__":
    raise SystemExit(main())
