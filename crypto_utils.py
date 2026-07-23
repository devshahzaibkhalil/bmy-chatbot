"""
crypto_utils.py
Encryption at rest for customer PII (name, email, phone, company, website).

Uses Fernet (AES-128-CBC + HMAC, from the `cryptography` package) for
encryption/decryption, and a separate HMAC-SHA256 "blind index" for exact
lookups (e.g. finding a returning customer by email) - Fernet output is
randomized per-call, so it can't be searched directly, which is why the
blind index exists alongside it. No external service is involved; both
keys are generated locally on first run and stored under database/.keys/.
"""

import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken

from config import Config

_fernet = None


def _get_fernet():
    global _fernet
    if _fernet is None:
        _fernet = Fernet(Config.ENCRYPTION_KEY)
    return _fernet


def encrypt(value):
    """Returns ciphertext (str), or the input unchanged if it's None/empty."""
    if value is None or value == "":
        return value
    return _get_fernet().encrypt(str(value).encode("utf-8")).decode("utf-8")


def decrypt(value):
    """
    Returns plaintext (str). If the value isn't valid ciphertext (e.g. it's
    legacy plaintext data from before encryption was added, or already
    decrypted), returns it unchanged rather than raising - PII decryption
    failures should never crash the dashboard.
    """
    if value is None or value == "":
        return value
    try:
        return _get_fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeDecodeError, AttributeError):
        return value


def blind_index(value):
    """
    Deterministic HMAC-SHA256 of a normalized (trimmed, lowercased) value,
    used only for exact-match lookups (returning-customer recognition).
    Not reversible - this is not encryption, just a searchable fingerprint.
    """
    if not value:
        return None
    normalized = str(value).strip().lower()
    return hmac.new(
        Config.BLIND_INDEX_KEY.encode("utf-8"),
        normalized.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
