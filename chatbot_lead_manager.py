r"""
chatbot_lead_manager.py
Standalone state-machine style lead-capture helper: watches for booking/
consultation keywords, then collects name, email, phone, and goals turn by
turn before saving a lead.

Note: this is now the THIRD lead-capture implementation in this project,
after:
  - chat_engine.py's built-in guided purchase/qualification flow (staged
    questions driven by knowledge/purchase_flow.json, already wired into
    app.py and actively used), and
  - lead_collector.py (an earlier standalone LeadCollector class, also
    not wired in).
None of the three run automatically - only chat_engine.py's flow is
actually called from app.py today. If you want lead capture to work in
the live app, chat_engine.py's flow is already doing that; treat this
file and lead_collector.py as reference implementations / alternatives,
not additional active behavior, unless you explicitly wire one in.

Differences from chat_engine.py's flow worth knowing before choosing:
  - No email/phone format validation here (chat_engine.py + validation/
    email_validator.py and phone_validator.py do validate). The phone
    regex here (`^\+?[0-9\s\-]{7,15}$`) only checks shape, not that the
    number is dialable.
  - Doesn't check whether the lead already has an interested service,
    business name, or existing customer/conversation record - it starts
    fresh state per instance.
"""

import re

from database import db


class ChatbotLeadManager:
    def __init__(self, customer_id=None, conversation_id=None):
        self.state = "IDLE"  # States: IDLE, AWAITING_LEAD_INFO
        self.lead_data = {
            "name": None,
            "email": None,
            "phone": None,
            "goals": None
        }
        # Needed to actually persist a lead via database.db.create_lead,
        # which requires these two IDs - pass them in from whatever
        # already-running conversation this manager is tracking.
        self.customer_id = customer_id
        self.conversation_id = conversation_id

    def process_message(self, user_input: str) -> str:
        text = user_input.strip()

        # 1. If we are currently collecting contact info
        if self.state == "AWAITING_LEAD_INFO":
            return self._handle_lead_input(text)

        # 2. Trigger lead collection if user asks for consultation/contact
        booking_keywords = ["schedule", "consultation", "book", "contact", "get in touch", "hire"]
        if any(keyword in text.lower() for keyword in booking_keywords):
            self.state = "AWAITING_LEAD_INFO"
            return (
                "We'd be happy to schedule a consultation! "
                "Please provide your name, email, phone number, and a brief description of your goals."
            )

        # 3. Regular FAQ/Query Lookup
        response = self._lookup_faq(text)
        if response:
            return response

        # 4. Fallback if query isn't recognized
        return (
            "I specialize in questions about BMyMarketer's services, pricing, and process. "
            "Could you rephrase your question, or let me know if you'd like to schedule a consultation?"
        )

    def _handle_lead_input(self, text: str) -> str:
        """Extracts whatever piece of data the user provided."""

        # Check for Email
        if "@" in text and "." in text:
            self.lead_data["email"] = text

        # Check for Phone Number (digits, spaces, or plus)
        elif re.search(r'^\+?[0-9\s\-]{7,15}$', text):
            self.lead_data["phone"] = text

        # If no name exists yet, assume text is the user's Name
        elif not self.lead_data["name"]:
            self.lead_data["name"] = text.title()

        # Otherwise, save as project goals
        elif not self.lead_data["goals"]:
            self.lead_data["goals"] = text

        return self._get_next_lead_prompt()

    def _get_next_lead_prompt(self) -> str:
        name = self.lead_data["name"]
        email = self.lead_data["email"]
        phone = self.lead_data["phone"]

        # User gave ONLY Name
        if name and not email and not phone:
            return f"Thanks, {name}! Could you also share your **email address** or **phone number** so a team member can contact you?"

        # User gave Name + Email, but no Phone
        elif name and email and not phone:
            return f"Got it, {name}! Lastly, what is your **phone number** (with country code) and a brief description of your goals?"

        # User gave Name + Phone, but no Email
        elif name and phone and not email:
            return f"Thanks, {name}! What is your **email address** so we can send over details?"

        # Missing Name entirely
        elif not name:
            return "Thank you! Could you please tell me your **full name**?"

        # All details collected!
        else:
            self.state = "IDLE"
            self._save_lead_to_db()
            return f"Awesome, {name}! We've recorded your details. A team member from BMyMarketer will reach out to you shortly."

    def _lookup_faq(self, text: str):
        # This project's real FAQ matching lives in chat_engine.py
        # (ChatEngine.respond / _match_service / _search_documents), which
        # is tightly coupled to per-conversation state (interested
        # service, pending pricing topic, etc.) rather than being a
        # simple text-in/text-out function. Wiring this stub up to that
        # engine properly means running this class as part of the same
        # request flow as ChatEngine, not standalone - left as a stub
        # rather than a fragile partial integration.
        return None

    def _save_lead_to_db(self):
        # Persists via the project's real leads table (database/db.py).
        # create_lead requires customer_id/conversation_id to link the
        # lead to a conversation - pass them into __init__ from the
        # active session/request if you wire this class into app.py.
        if not (self.customer_id and self.conversation_id):
            return None
        return db.create_lead(
            customer_id=self.customer_id,
            conversation_id=self.conversation_id,
            name=self.lead_data.get("name"),
            email=self.lead_data.get("email"),
            phone=self.lead_data.get("phone"),
            conversation_summary=self.lead_data.get("goals"),
        )
