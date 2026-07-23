"""
validation/phone_validator.py
Lightweight, dependency-free phone number validation used across the BMY
Marketer chatbot (guided purchase flow, /api/chat/start, etc), matching the
style of validation/email_validator.py.

Checks performed, in order:
  1. Presence
  2. Format (allowed characters: digits, +, -, (), spaces; 7-20 chars)
  3. Digit count (7-15 digits, per E.164 practical range)
  4. Obviously-fake numbers (all same digit, sequential placeholders like
     1234567890, common junk values like 0000000000)
"""

import re

PHONE_REGEX = re.compile(r"^\+?[0-9\-\(\)\s]{7,20}$")

_FAKE_NUMBERS = {
    "1234567",
    "12345678",
    "123456789",
    "1234567890",
    "0123456789",
    "0000000000",
    "1111111111",
    "9999999999",
}


def validate_phone(phone):
    """
    Returns:
        {
            "valid": bool,
            "message": str,
            "phone": str
        }
    """
    if not phone:
        return {
            "valid": False,
            "message": "Please enter your phone number.",
            "phone": "",
        }

    phone = phone.strip()

    # Check allowed characters
    if not PHONE_REGEX.fullmatch(phone):
        return {
            "valid": False,
            "message": "Please enter a valid phone number.",
            "phone": phone,
        }

    # Remove everything except digits
    digits = re.sub(r"\D", "", phone)

    if len(digits) < 7:
        return {
            "valid": False,
            "message": "Phone number is too short.",
            "phone": phone,
        }

    if len(digits) > 15:
        return {
            "valid": False,
            "message": "Phone number is too long.",
            "phone": phone,
        }

    # Reject fake numbers (all repeated digit, e.g. 1111111111)
    if digits == digits[0] * len(digits):
        return {
            "valid": False,
            "message": "Please enter a valid phone number.",
            "phone": phone,
        }

    if digits in _FAKE_NUMBERS:
        return {
            "valid": False,
            "message": "Please enter a real phone number.",
            "phone": phone,
        }

    return {
        "valid": True,
        "message": "Valid phone number.",
        "phone": phone,
    }


if __name__ == "__main__":
    # Quick manual smoke test: python -m validation.phone_validator
    phones = [
        "+1 212 555 1234",
        "+92 3001234567",
        "03001234567",
        "1234567890",
        "1111111111",
        "abc123",
        "123",
    ]
    for p in phones:
        print(p, "->", validate_phone(p))
