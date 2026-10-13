"""Account management from the command line (first admin, lost passwords).

    python -m hub.users create --email you@equinix.com --name "Your Name" --role admin
    python -m hub.users reset-password --email you@equinix.com
    python -m hub.users list
"""
from __future__ import annotations

import argparse
import getpass
import sys

from fastapi import HTTPException
from sqlalchemy import select

from . import auth
from .db import init_engine, session
from .models import ROLES, User


def _password(args) -> str:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\n")
    pw = getpass.getpass("Password: ")
    if pw != getpass.getpass("Again: "):
        sys.exit("passwords differ")
    return pw


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m hub.users")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--email", required=True)
    c.add_argument("--name", default="")
    c.add_argument("--role", default="admin", choices=ROLES)
    c.add_argument("--password-stdin", action="store_true")
    r = sub.add_parser("reset-password")
    r.add_argument("--email", required=True)
    r.add_argument("--password-stdin", action="store_true")
    sub.add_parser("list")
    args = p.parse_args(argv)
    init_engine()
    with session() as s:
        try:
            if args.cmd == "create":
                u = auth.create_user(s, email=args.email, name=args.name, role=args.role, password=_password(args))
                auth.audit(s, None, "user.create", "user", u.id, {"email": u.email, "role": u.role}, actor="cli")
                print(f"created {u.email} ({u.role})")
            elif args.cmd == "reset-password":
                u = s.scalar(select(User).where(User.email == args.email.strip().lower()))
                if u is None:
                    sys.exit("no such user")
                pw = _password(args)
                auth.check_password_policy(pw)
                u.password_hash = auth.hash_password(pw)
                u.password_changed_at = auth._now()
                u.active = 1
                auth.end_sessions(s, u.id)
                auth.audit(s, None, "user.password_reset", "user", u.id, {"email": u.email}, actor="cli")
                print(f"password reset for {u.email}; their sessions were signed out")
            else:
                for u in s.scalars(select(User).order_by(User.email)):
                    print(f"{u.email:40} {u.role:9} {'active' if u.active else 'disabled'}")
            s.commit()
        except HTTPException as exc:
            sys.exit(str(exc.detail))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
