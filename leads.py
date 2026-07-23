"""
leads.py
Detects buying-intent signals in customer messages and automatically
creates a lead record in the CRM. Pure rule-based logic - no external API.
"""

import re

from database import db

_INTENT_PATTERNS = [
    "interested in", "i want to", "i'd like to", "i need", "sign me up",
    "get started", "hire you", "work with you", "quote", "quotation",
    "proposal", "book a call", "schedule a call", "consultation",
    "how do we start", "ready to start", "let's do this", "i'm ready",
    "purchase", "purchased", "buy", "checkout", "check out",
    "place an order", "place order", "make a payment", "get billed",
    "invoice", "how do i pay", "how can i pay", "want to proceed",
    "ready to proceed",
]

_BUDGET_REGEX = re.compile(
    r"(\$\s?\d[\d,]*(\.\d+)?\s?(k|K)?|budget of \d[\d,]*|\d[\d,]*\s?(usd|dollars))"
)

_TIMELINE_PATTERNS = [
    "asap", "this week", "this month", "next month", r"in \d+ (days|weeks|months)",
    "immediately", "urgent", "as soon as possible",
]


def has_buying_intent(text):
    clean = text.lower()
    return any(p in clean for p in _INTENT_PATTERNS)


def extract_budget(text):
    match = _BUDGET_REGEX.search(text)
    return match.group(0) if match else None


def extract_timeline(text):
    clean = text.lower()
    for pattern in _TIMELINE_PATTERNS:
        match = re.search(pattern, clean)
        if match:
            return match.group(0)
    return None


def build_conversation_summary(conversation_id, max_messages=6):
    """Short summary from the most recent customer messages, for the lead record."""
    messages = db.get_conversation_messages(conversation_id)
    customer_lines = [m["message"] for m in messages if m["sender"] == "customer"]
    recent = customer_lines[-max_messages:]
    return " | ".join(recent) if recent else ""


def maybe_create_lead(conversation_id, customer_id, latest_message, interested_service=None):
    """
    Called after every customer message. If buying intent is detected and no
    lead exists yet for this conversation, create one using whatever contact
    info the customer record has so far.
    """
    if not has_buying_intent(latest_message):
        return None
    if db.lead_exists_for_conversation(conversation_id):
        return None

    customer = db.get_customer(customer_id) if customer_id else None

    budget = extract_budget(latest_message)
    timeline = extract_timeline(latest_message)

    if budget or timeline:
        db.update_conversation_intent(conversation_id, budget=budget, timeline=timeline)

    lead_id = db.create_lead(
        customer_id=customer_id,
        conversation_id=conversation_id,
        name=customer.get("full_name") if customer else None,
        email=customer.get("email") if customer else None,
        phone=customer.get("phone") if customer else None,
        company_name=customer.get("company_name") if customer else None,
        interested_service=interested_service,
        budget=budget,
        timeline=timeline,
        conversation_summary=build_conversation_summary(conversation_id),
    )
    db.log_event("new_lead", conversation_id=conversation_id, payload=lead_id)
    return lead_id