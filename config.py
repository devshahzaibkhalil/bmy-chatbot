"""
config.py
Central configuration for BMY Marketer AI Assistant.
No external API keys are used anywhere in this project.
"""

import os
import secrets
from datetime import timedelta

from cryptography.fernet import Fernet

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KEYS_DIR = os.path.join(BASE_DIR, "database", ".keys")


def _load_or_create_key(env_var, filename, generator):
    """
    Priority: explicit environment variable > a key file already on disk >
    freshly generated key (persisted to disk so restarts keep working).

    Auto-generating and storing locally is a reasonable default for a
    single-machine deployment. For production across multiple machines/
    processes, set the env var instead so every instance shares the same
    key - otherwise data encrypted by one instance won't decrypt on another.
    """
    from_env = os.environ.get(env_var)
    if from_env:
        return from_env

    os.makedirs(KEYS_DIR, exist_ok=True)
    key_path = os.path.join(KEYS_DIR, filename)
    if os.path.exists(key_path):
        with open(key_path, "r", encoding="utf-8") as f:
            return f.read().strip()

    new_key = generator()
    with open(key_path, "w", encoding="utf-8") as f:
        f.write(new_key)
    return new_key


class Config:
    # --- General ---
    SECRET_KEY = _load_or_create_key(
        "SECRET_KEY", "flask_secret.key", lambda: secrets.token_hex(32)
    )
    DEBUG = os.environ.get("FLASK_DEBUG", "1") == "1"

    # --- Paths ---
    KNOWLEDGE_DIR = os.path.join(BASE_DIR, "knowledge")
    WEBSITE_DATA_PATH = os.path.join(KNOWLEDGE_DIR, "website_data.json")
    FAQ_DATA_DIR = os.path.join(KNOWLEDGE_DIR, "faqs")
    PURCHASE_FLOW_PATH = os.path.join(KNOWLEDGE_DIR, "purchase_flow.json")
    SERVICE_FAQS_PATH = os.path.join(KNOWLEDGE_DIR, "service_faqs.json")
    UPLOADS_DIR = os.path.join(BASE_DIR, "uploads", "documents")
    BACKUPS_DIR = os.path.join(BASE_DIR, "backups")
    DATABASE_PATH = os.path.join(BASE_DIR, "database", "bmy_chatbot.db")

    # --- Encryption at rest (customer PII) ---
    # ENCRYPTION_KEY must be a valid Fernet key; BLIND_INDEX_KEY is any secret string.
    ENCRYPTION_KEY = _load_or_create_key(
        "BMY_ENCRYPTION_KEY", "encryption.key", lambda: Fernet.generate_key().decode("utf-8")
    )
    BLIND_INDEX_KEY = _load_or_create_key(
        "BMY_BLIND_INDEX_KEY", "blind_index.key", lambda: secrets.token_hex(32)
    )

    # --- Chatbot NLP matching ---
    # Minimum fuzzy match score (0-100) to accept a FAQ/knowledge answer directly
    FUZZY_MATCH_THRESHOLD = 72
    # Score below this is treated as "no confident answer" -> fallback + escalation flag
    FUZZY_FALLBACK_THRESHOLD = 55

    # --- Session ---
    # Flask only expires a session after this long if session.permanent is
    # set to True at login (see admin_routes.py) - PERMANENT_SESSION_LIFETIME
    # is the actual Flask config key this maps to.
    PERMANENT_SESSION_LIFETIME = timedelta(hours=6)
    # Cookies only travel over HTTPS in production. Defaults to "on" whenever
    # debug mode is off (i.e. you're running this for real, not locally on
    # http://localhost) - set BMY_FORCE_HTTP_COOKIES=1 to override if you're
    # intentionally serving plain HTTP in production behind a trusted proxy
    # that already terminates TLS and you understand the tradeoff.
    SESSION_COOKIE_SECURE = (
        False if os.environ.get("BMY_FORCE_HTTP_COOKIES") == "1"
        else not (os.environ.get("FLASK_DEBUG", "1") == "1")
    )

    # --- CORS ---
    CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*")

    # --- Rate limiting (chat endpoints) ---
    RATE_LIMIT_PER_MINUTE = int(os.environ.get("BMY_RATE_LIMIT_PER_MINUTE", "30"))
    # Optional - only used if set AND the `redis` package is installed.
    # Needed once you run more than one app process/worker; unset = in-memory limiter.
    REDIS_URL = os.environ.get("BMY_REDIS_URL")


def ensure_directories():
    """Create required folders on first run if they don't exist."""
    for path in [
        Config.UPLOADS_DIR,
        Config.BACKUPS_DIR,
        os.path.dirname(Config.DATABASE_PATH),
        KEYS_DIR,
    ]:
        os.makedirs(path, exist_ok=True)
