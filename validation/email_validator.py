"""
validation/email_validator.py
Lightweight, dependency-free email validation used across the BMY Marketer
chatbot (guided purchase flow, /api/chat/start, etc).

Checks performed, in order:
  1. Presence / length
  2. Format (RFC-ish regex)
  3. Common domain typos (gmial.com -> gmail.com, ...) - returns a suggestion
     instead of silently accepting or silently rejecting
  4. Disposable/temporary email domains - blocked outright
"""

import re

# Regular expression for email validation
EMAIL_REGEX = re.compile(
    r"^(?=.{1,254}$)(?=.{1,64}@)[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"
)

# Common email domain typos
COMMON_TYPOS = {
    "gmial.com": "gmail.com",
    "gmai.com": "gmail.com",
    "gmail.con": "gmail.com",
    "gmail.co": "gmail.com",
    "hotnail.com": "hotmail.com",
    "hotmai.com": "hotmail.com",
    "yahooo.com": "yahoo.com",
    "yahho.com": "yahoo.com",
    "outlok.com": "outlook.com",
    "outllok.com": "outlook.com",
}

# Disposable email domains
DISPOSABLE_DOMAINS = {
    "10minutemail.com",
    "mailinator.com",
    "guerrillamail.com",
    "tempmail.com",
    "throwawaymail.com",
    "trashmail.com",
    "yopmail.com",
}


def validate_email(email):
    """
    Returns:
        {
            "valid": bool,
            "message": str,
            "email": str,
            "suggestion": str | None
        }
    """
    if not email:
        return {
            "valid": False,
            "message": "Email address is required.",
            "email": "",
            "suggestion": None,
        }

    email = email.strip().lower()

    # Check overall length
    if len(email) > 254:
        return {
            "valid": False,
            "message": "Email address is too long.",
            "email": email,
            "suggestion": None,
        }

    # Check format
    if not EMAIL_REGEX.fullmatch(email):
        return {
            "valid": False,
            "message": "Please enter a valid email address (e.g. name@example.com).",
            "email": email,
            "suggestion": None,
        }

    username, domain = email.split("@", 1)

    # Suggest correction for common typos
    if domain in COMMON_TYPOS:
        corrected = f"{username}@{COMMON_TYPOS[domain]}"
        return {
            "valid": False,
            "message": f"Did you mean '{corrected}'?",
            "email": email,
            "suggestion": corrected,
        }

    # Block disposable email providers
    if domain in DISPOSABLE_DOMAINS:
        return {
            "valid": False,
            "message": "Please use a permanent email address instead of a temporary email.",
            "email": email,
            "suggestion": None,
        }

    return {
        "valid": True,
        "message": "Email is valid.",
        "email": email,
        "suggestion": None,
    }


if __name__ == "__main__":
    # Quick manual smoke test: python -m validation.email_validator
    emails = [
        "john@gmail.com",
        "john@gmial.com",
        "john@gmail",
        "john@yahooo.com",
        "abc@10minutemail.com",
        "info@bmymarketer.com",
    ]
    for e in emails:
        print(validate_email(e))
