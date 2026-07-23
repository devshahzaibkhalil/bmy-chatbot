"""
notifications.py
Creates in-dashboard notifications for events the admin should know about:
new conversation, new lead, quote request, appointment booked, and
unanswered questions. Notifications are stored locally in SQLite and shown
via the bell icon in the admin dashboard - no external push/email service
is required to use this.

An optional local SMTP email hook is included for admins who want an email
copy too, but it's off by default (no email credentials are stored or
required unless explicitly configured).
"""

import os
import smtplib
from email.message import EmailMessage

from database import db

_TITLES = {
    "new_conversation": "New conversation started",
    "new_lead": "New lead created",
    "quote_request": "Customer requested a quote",
    "appointment_booked": "New consultation requested",
    "unanswered_question": "Chatbot couldn't answer a question",
    "file_uploaded": "Customer uploaded a file",
}

# Optional - only used if all three env vars are set. Nothing is sent otherwise.
_SMTP_HOST = os.environ.get("BMY_SMTP_HOST")
_SMTP_FROM = os.environ.get("BMY_SMTP_FROM")
_SMTP_TO = os.environ.get("BMY_ADMIN_NOTIFY_EMAIL")


def notify(event_type, conversation_id=None, message=None):
    """
    Records the analytics event AND creates a dashboard notification.
    Returns the notification id.
    """
    db.log_event(event_type, conversation_id=conversation_id, payload=message)
    title = _TITLES.get(event_type, event_type.replace("_", " ").title())
    notif_id = db.create_notification(event_type, title, message, conversation_id)

    if _SMTP_HOST and _SMTP_FROM and _SMTP_TO:
        _send_email_best_effort(title, message)

    return notif_id


def _send_email_best_effort(subject, body):
    """Fire-and-forget local SMTP send. Never raises - a notification failure
    should never break the chat flow."""
    try:
        msg = EmailMessage()
        msg["Subject"] = f"[BMyMarketer Assistant] {subject}"
        msg["From"] = _SMTP_FROM
        msg["To"] = _SMTP_TO
        msg.set_content(body or subject)
        with smtplib.SMTP(_SMTP_HOST, timeout=5) as server:
            server.send_message(msg)
    except Exception:
        pass  # notifications are best-effort; the dashboard bell is the source of truth
