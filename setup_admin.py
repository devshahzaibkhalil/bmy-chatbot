"""
setup_admin.py
Run this once to create the first admin login for the dashboard:

    python setup_admin.py

It will prompt for a username and password, hash the password, and store
the admin user in the local SQLite database. No data leaves your machine.
"""

import getpass
import sys

from config import ensure_directories
from database import db
from auth import hash_password

ensure_directories()
db.init_db()


def main():
    print("=== BMY Marketer AI Assistant - Admin Setup ===")

    if db.any_admin_exists():
        confirm = input("An admin account already exists. Create another one? (y/N): ").strip().lower()
        if confirm != "y":
            print("Cancelled.")
            sys.exit(0)

    username = input("Choose an admin username: ").strip()
    if not username:
        print("Username cannot be empty.")
        sys.exit(1)

    if db.get_admin_by_username(username):
        print(f"Username '{username}' already exists.")
        sys.exit(1)

    password = getpass.getpass("Choose an admin password: ")
    confirm_password = getpass.getpass("Confirm password: ")
    if password != confirm_password:
        print("Passwords do not match.")
        sys.exit(1)
    if len(password) < 8:
        print("Password must be at least 8 characters.")
        sys.exit(1)

    print("Roles: agent (view/reply/notes only), admin (+ manage leads/knowledge base/delete),")
    print("       superadmin (+ backups, admin user management)")
    role = input("Role [agent/admin/superadmin] (default: agent): ").strip() or "agent"
    if role not in ("agent", "admin", "superadmin"):
        print("Role must be agent, admin, or superadmin.")
        sys.exit(1)

    db.create_admin_user(username, hash_password(password), role=role)
    print(f"\nAdmin user '{username}' created with role '{role}'.")
    print("You can now log in at /admin/login")


if __name__ == "__main__":
    main()
