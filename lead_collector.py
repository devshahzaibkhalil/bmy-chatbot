"""
lead_collector.py
Standalone conversational lead-capture helper. Given free-text user
messages, guesses whether the user is giving a name, email, or phone
number, validates email/phone via validation.input_validator.InputValidator,
and prompts for whatever contact detail is still missing.

Note: this is a separate, simpler flow from the guided purchase/qualification
flow already implemented in chat_engine.py (which collects business_name,
full_name, email, phone, etc. as staged questions driven by
knowledge/purchase_flow.json, using validation.email_validator /
validation.phone_validator). This module is not currently wired into
chat_engine.py or app.py - use it if you want a lighter-weight, ad-hoc
alternative to that flow, or adapt the pieces you need into chat_engine.py.
"""

from validation.input_validator import InputValidator


class LeadCollector:
    def __init__(self):
        # Store collected user data
        self.lead_data = {
            "name": None,
            "email": None,
            "phone": None
        }

    def process_input(self, user_text: str) -> str:
        """
        Dynamically captures Name, Email, or Phone and asks for whatever is missing.
        """
        cleaned_text = user_text.strip()

        # 1. Try to validate as Email
        if "@" in cleaned_text and "." in cleaned_text:
            is_valid, result = InputValidator.validate_email(cleaned_text)
            if is_valid:
                self.lead_data["email"] = cleaned_text
            else:
                return f"{result} Please provide a valid email address."

        # 2. Try to validate as Phone Number
        elif any(char.isdigit() for char in cleaned_text) and len(cleaned_text) >= 7:
            is_valid, result = InputValidator.validate_phone(cleaned_text)
            if is_valid:
                self.lead_data["phone"] = result  # Clean E.164 phone
            else:
                return f"{result} Please provide a valid phone number with country code."

        # 3. Otherwise, assume it's a Name (if not already set)
        elif not self.lead_data["name"]:
            self.lead_data["name"] = cleaned_text.title()

        # 4. Check what details are missing and prompt the user next
        return self._get_next_prompt()

    def _get_next_prompt(self) -> str:
        name = self.lead_data["name"]
        email = self.lead_data["email"]
        phone = self.lead_data["phone"]

        # Case A: Got Name, need Email & Phone
        if name and not email and not phone:
            return f"Nice to meet you, {name}! Could you please share your **email address** or **phone number** so our team can get in touch?"

        # Case B: Got Name & Email, still need Phone
        elif name and email and not phone:
            return f"Thanks, {name}! Lastly, please provide your **phone number** (with country code) so we can send you project updates."

        # Case C: Got Name & Phone, still need Email
        elif name and phone and not email:
            return "Got it! Could you also share your **email address** so we can send over our proposal?"

        # Case D: Got Email/Phone first, still missing Name
        elif (email or phone) and not name:
            return "Thank you! May I also know your **full name**?"

        # Case E: All details collected successfully!
        else:
            return f"Awesome! Thank you, {name}. We have saved your contact details (Email: {email}, Phone: {phone}). How else can we assist you today?"
