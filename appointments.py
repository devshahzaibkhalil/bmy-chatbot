"""
appointments.py
Detects scheduling/consultation requests in customer messages and creates
an appointment record (status: requested) for the admin to confirm.
Rule-based, no external calendar API.
"""

import re

from database import db

_SCHEDULING_PATTERNS = [
    "book a call", "schedule a call", "book a consultation", "schedule a consultation",
    "book an appointment", "schedule an appointment", "set up a call",
    "can we schedule", "can we set up a time", "available to talk",
    "book a meeting", "schedule a meeting",
]

_WHEN_REGEX = re.compile(
    r"(tomorrow|today|next week|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"\d{1,2}(:\d{2})?\s?(am|pm)|\d{1,2}/\d{1,2}(/\d{2,4})?)",
    re.IGNORECASE,
)


def is_scheduling_request(text):
    clean = text.lower()
    return any(p in clean for p in _SCHEDULING_PATTERNS)


def extract_when(text):
    match = _WHEN_REGEX.search(text)
    return match.group(0) if match else None


def maybe_create_appointment(conversation_id, customer_id, latest_message):
    """
    Called after every customer message. If the message requests scheduling,
    creates an appointment (status: requested) with whatever timing info was
    mentioned, so the admin can confirm a specific slot.
    """
    if not is_scheduling_request(latest_message):
        return None

    when = extract_when(latest_message)
    appt_id = db.create_appointment(
        customer_id=customer_id,
        conversation_id=conversation_id,
        scheduled_for=when,
        notes=latest_message,
    )
    return appt_id
