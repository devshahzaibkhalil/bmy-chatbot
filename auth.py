"""
auth.py
Session-based admin authentication for the BMY Marketer AI Assistant.

Passwords are hashed with werkzeug's PBKDF2 implementation (no plaintext
storage). No third-party auth service or API is used - this is a self
contained, local admin login backed by the admin_users SQLite table.
"""

from functools import wraps

from flask import session, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

from database import db


def hash_password(plain_password):
    return generate_password_hash(plain_password)


def verify_password(plain_password, password_hash):
    return check_password_hash(password_hash, plain_password)


def attempt_login(username, password):
    """Returns the admin user dict on success, None on failure."""
    user = db.get_admin_by_username(username)
    if not user:
        return None
    if not verify_password(password, user["password_hash"]):
        return None
    return user


def login_required(view_func):
    """Protects admin API routes - requires an authenticated session."""
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("admin_username"):
            return jsonify({"error": "Authentication required."}), 401
        return view_func(*args, **kwargs)
    return wrapped


def role_required(*allowed_roles):
    """
    Protects routes that need a specific admin role, e.g. @role_required("superadmin")
    or @role_required("admin", "superadmin") for "either of these".
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not session.get("admin_username"):
                return jsonify({"error": "Authentication required."}), 401
            if session.get("admin_role") not in allowed_roles:
                return jsonify({"error": "Insufficient permissions."}), 403
            return view_func(*args, **kwargs)
        return wrapped
    return decorator
