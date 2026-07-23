"""
manage_admin.py
Quick admin-account management for the BMY Marketer AI Assistant.

This is the fast, scriptable companion to setup_admin.py - use it any time
you need to create the first admin account, change a username, reset a
forgotten password, or remove an account, without answering interactive
prompts. All commands work directly against the local SQLite database.

USAGE
-----
Create the first (or an additional) admin account:
    python manage_admin.py create --username admin --password "MyStrongPass123" --role superadmin

Reset a password (fixes "Invalid username or password" lockouts):
    python manage_admin.py reset-password --username admin --password "NewStrongPass123"

Rename a login:
    python manage_admin.py rename --username admin --new-username owner

List all admin accounts:
    python manage_admin.py list

Delete an account:
    python manage_admin.py delete --username agent1

Roles: agent (view/reply/notes only), admin (+ leads/knowledge base/delete),
superadmin (+ backups, team/admin-user management).

Password rules: minimum 8 characters. Passwords are hashed with werkzeug's
PBKDF2 (see auth.py) - nothing is ever stored in plaintext.
"""

import argparse
import sys

from config import ensure_directories
from database import db
from auth import hash_password

ensure_directories()
db.init_db()

VALID_ROLES = ("agent", "admin", "superadmin")


def cmd_create(args):
    username = args.username.strip()
    if not username:
        sys.exit("Username cannot be empty.")
    if len(args.password) < 8:
        sys.exit("Password must be at least 8 characters.")
    if args.role not in VALID_ROLES:
        sys.exit(f"Role must be one of: {', '.join(VALID_ROLES)}")
    if db.get_admin_by_username(username):
        sys.exit(f"Username '{username}' already exists. Use reset-password to change its password.")

    db.create_admin_user(username, hash_password(args.password), role=args.role)
    print(f"Created admin '{username}' with role '{args.role}'.")
    print("Log in at /admin/login")


def cmd_reset_password(args):
    user = db.get_admin_by_username(args.username)
    if not user:
        sys.exit(f"No admin account named '{args.username}'. Use 'create' to make one.")
    if len(args.password) < 8:
        sys.exit("Password must be at least 8 characters.")

    with db.get_conn() as conn:
        conn.execute(
            "UPDATE admin_users SET password_hash = ? WHERE username = ?",
            (hash_password(args.password), args.username),
        )
    print(f"Password updated for '{args.username}'.")


def cmd_rename(args):
    user = db.get_admin_by_username(args.username)
    if not user:
        sys.exit(f"No admin account named '{args.username}'.")
    new_username = args.new_username.strip()
    if not new_username:
        sys.exit("New username cannot be empty.")
    if db.get_admin_by_username(new_username):
        sys.exit(f"Username '{new_username}' is already taken.")

    with db.get_conn() as conn:
        conn.execute(
            "UPDATE admin_users SET username = ? WHERE username = ?",
            (new_username, args.username),
        )
    print(f"Renamed '{args.username}' to '{new_username}'.")


def cmd_list(args):
    users = db.list_admin_users()
    if not users:
        print("No admin accounts yet. Create one with:")
        print('  python manage_admin.py create --username admin --password "YourPassword123" --role superadmin')
        return
    print(f"{'Username':<20} {'Role':<12} {'Created':<20}")
    print("-" * 52)
    for u in users:
        print(f"{u['username']:<20} {u['role']:<12} {u['created_at']:<20}")


def cmd_delete(args):
    user = db.get_admin_by_username(args.username)
    if not user:
        sys.exit(f"No admin account named '{args.username}'.")
    if not args.yes:
        confirm = input(f"Delete admin account '{args.username}'? This cannot be undone. (y/N): ").strip().lower()
        if confirm != "y":
            print("Cancelled.")
            return
    db.delete_admin_user(user["id"])
    print(f"Deleted '{args.username}'.")


def build_parser():
    parser = argparse.ArgumentParser(
        description="Create, reset, rename, or delete BMY Marketer admin accounts."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", help="Create a new admin account")
    p_create.add_argument("--username", required=True)
    p_create.add_argument("--password", required=True)
    p_create.add_argument("--role", default="superadmin", choices=VALID_ROLES)
    p_create.set_defaults(func=cmd_create)

    p_reset = sub.add_parser("reset-password", help="Reset a forgotten/lost password")
    p_reset.add_argument("--username", required=True)
    p_reset.add_argument("--password", required=True)
    p_reset.set_defaults(func=cmd_reset_password)

    p_rename = sub.add_parser("rename", help="Change an admin's username/login")
    p_rename.add_argument("--username", required=True, help="Current username")
    p_rename.add_argument("--new-username", required=True)
    p_rename.set_defaults(func=cmd_rename)

    p_list = sub.add_parser("list", help="List all admin accounts")
    p_list.set_defaults(func=cmd_list)

    p_delete = sub.add_parser("delete", help="Delete an admin account")
    p_delete.add_argument("--username", required=True)
    p_delete.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")
    p_delete.set_defaults(func=cmd_delete)

    return parser


if __name__ == "__main__":
    parser = build_parser()
    parsed_args = parser.parse_args()
    parsed_args.func(parsed_args)
