"""Admin CLI: create users or reset passwords directly against the DB.

Usage:
  python -m app.cli reset-password --email owner@console.app
  python -m app.cli reset-password --email owner@console.app --password mypass
  python -m app.cli add-user --email ops@example.com --role operator
  python -m app.cli list-users

If --password is omitted, a random one is generated and printed once.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import secrets
import sqlite3
import sys

from .config import get_settings
from .db import _connect, init_db
from .security import hash_password


_ROLES = ("owner", "operator", "viewer")


def _conn() -> sqlite3.Connection:
    s = get_settings()
    init_db()
    return _connect(s.db_path)


def cmd_reset_password(args: argparse.Namespace) -> int:
    pwd = args.password or secrets.token_urlsafe(12)
    con = _conn()
    try:
        cur = con.execute(
            "UPDATE users SET password_hash=? WHERE email=?",
            (hash_password(pwd), args.email.lower()),
        )
        con.commit()
        if cur.rowcount == 0:
            print(f"no such user: {args.email}", file=sys.stderr)
            return 2
    finally:
        con.close()
    print(f"password for {args.email} reset to: {pwd}")
    return 0


def cmd_add_user(args: argparse.Namespace) -> int:
    if args.role not in _ROLES:
        print(f"role must be one of {_ROLES}", file=sys.stderr)
        return 2
    pwd = args.password or secrets.token_urlsafe(12)
    con = _conn()
    try:
        try:
            con.execute(
                "INSERT INTO users (email, password_hash, role, is_active, created_at) "
                "VALUES (?,?,?,1,?)",
                (
                    args.email.lower(),
                    hash_password(pwd),
                    args.role,
                    _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
                ),
            )
            con.commit()
        except sqlite3.IntegrityError:
            print(f"user already exists: {args.email}", file=sys.stderr)
            return 2
    finally:
        con.close()
    print(f"created {args.role} {args.email} with password: {pwd}")
    return 0


def cmd_list_users(_: argparse.Namespace) -> int:
    con = _conn()
    try:
        rows = con.execute(
            "SELECT id, email, role, is_active, created_at FROM users ORDER BY id"
        ).fetchall()
    finally:
        con.close()
    if not rows:
        print("(no users)")
        return 0
    for r in rows:
        print(f"#{r['id']}  {r['email']:32}  role={r['role']:8}  active={r['is_active']}  {r['created_at']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="koc-cli")
    sub = p.add_subparsers(dest="cmd", required=True)

    rp = sub.add_parser("reset-password", help="Reset password for an existing user.")
    rp.add_argument("--email", required=True)
    rp.add_argument("--password", default=None, help="If omitted, generate a random one.")
    rp.set_defaults(func=cmd_reset_password)

    au = sub.add_parser("add-user", help="Create a new user.")
    au.add_argument("--email", required=True)
    au.add_argument("--role", default="operator", choices=_ROLES)
    au.add_argument("--password", default=None)
    au.set_defaults(func=cmd_add_user)

    lu = sub.add_parser("list-users", help="List all users.")
    lu.set_defaults(func=cmd_list_users)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
