"""
lead_email_notify.py
Sends an email alert when a new lead is captured by the chatbot.

Note: notifications.py already implements this (notify("new_lead", ...) 
creates a dashboard notification AND, if BMY_SMTP_HOST / BMY_SMTP_FROM /
BMY_ADMIN_NOTIFY_EMAIL env vars are set, sends a best-effort SMTP email).
This module is a standalone alternative for a Gmail-specific setup using
smtplib.SMTP_SSL, kept separate rather than merged into notifications.py -
wire it in only if you specifically want a second/different email path.

IMPORTANT: the credentials and "from"/"to" addresses are read from
environment variables, never hardcoded in source. Never commit real
email addresses or app passwords into a Python file - they'd end up in
git history and in this repo forever. Set these before use:
  BMY_GMAIL_ADDRESS   - the Gmail account to send from
  BMY_GMAIL_APP_PASSWORD - a Gmail App Password (not your normal password)
  BMY_SALES_EMAIL     - where the lead notification should be sent
"""

import os
import smtplib
from email.message import EmailMessage

_GMAIL_ADDRESS = os.environ.get("BMY_GMAIL_ADDRESS")
_GMAIL_APP_PASSWORD = os.environ.get("BMY_GMAIL_APP_PASSWORD")
_SALES_EMAIL = os.environ.get("BMY_SALES_EMAIL")


def send_lead_notification(lead_data):
    """
    lead_data: dict with at least "name", "email", "phone"; "url" optional.
    Returns True if the email was sent, False if credentials aren't
    configured or sending failed.
    """
    if not (_GMAIL_ADDRESS and _GMAIL_APP_PASSWORD and _SALES_EMAIL):
        return False

    msg = EmailMessage()
    msg["Subject"] = f"New Lead Captured: {lead_data.get('name', 'Unknown')}"
    msg["From"] = _GMAIL_ADDRESS
    msg["To"] = _SALES_EMAIL

    body = f"""
    New Lead Details from Chatbot:
    -----------------------------
    Name: {lead_data.get('name', 'N/A')}
    Email: {lead_data.get('email', 'N/A')}
    Phone: {lead_data.get('phone', 'N/A')}
    Website/Details: {lead_data.get('url', 'N/A')}
    """
    msg.set_content(body)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(_GMAIL_ADDRESS, _GMAIL_APP_PASSWORD)
            smtp.send_message(msg)
        return True
    except Exception:
        return False
