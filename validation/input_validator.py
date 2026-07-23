"""
validation/input_validator.py
Combined email / phone / URL validator using external libraries
(dnspython, phonenumbers) plus a live network check for URLs.

Note: unlike validation/email_validator.py and validation/phone_validator.py
(which are dependency-free and do not touch the network), this validator:
  - performs a live DNS MX lookup for email domains
  - performs a live HEAD request to check if a URL is reachable
  - requires the `dnspython` and `phonenumbers` packages (see requirements.txt)

This is provided as a standalone module. It is not yet wired into
app.py / chat_engine.py, which currently import validate_email /
validate_phone from the lightweight validators. Swap the imports over
if you want this version (with live DNS/URL checks) used instead.
"""

import re
import urllib.request
import dns.resolver
import phonenumbers
from phonenumbers import NumberParseException


class InputValidator:

    @staticmethod
    def validate_email(email: str) -> tuple[bool, str]:
        """
        Validates email syntax, domain structure, and checks for real MX DNS records.
        """
        if not email or not isinstance(email, str):
            return False, "Please enter a valid email address (e.g., alex@company.com)."

        email = email.strip().lower()

        # 1. Strict RFC 5322 Regex Syntax Check
        pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        if not re.match(pattern, email):
            return False, "The email format appears incorrect. Please provide a valid email address (e.g., alex@company.com)."

        # 2. Extract Domain & Perform DNS MX Record Lookup
        domain = email.split('@')[1]
        try:
            mx_records = dns.resolver.resolve(domain, 'MX')
            if not mx_records:
                return False, f"The email domain '@{domain}' cannot receive emails. Please check for typos and provide a valid email."
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.Timeout):
            return False, f"The email domain '@{domain}' does not exist or is inactive. Please provide a correct email address."
        except Exception:
            # Fallback if DNS query fails on server network
            pass

        return True, "Email address is valid."

    @staticmethod
    def validate_phone(phone_str: str, default_region: str = "US") -> tuple[bool, str]:
        """
        Validates phone number using Google's libphonenumber library.
        Enforces international standards (E.164).
        """
        if not phone_str or not isinstance(phone_str, str):
            return False, "Please enter a valid phone number with your country code (e.g., +1 415 555 2671)."

        cleaned_phone = phone_str.strip()

        try:
            # Parse number with default region fallback
            parsed_number = phonenumbers.parse(cleaned_phone, default_region)

            # Check if valid phone number according to country rules
            if phonenumbers.is_valid_number(parsed_number):
                formatted_e164 = phonenumbers.format_number(
                    parsed_number, phonenumbers.PhoneNumberFormat.E164
                )
                return True, formatted_e164  # Returns standardized +14155552671

        except NumberParseException:
            pass

        return False, "The phone number provided is invalid. Please include your country code (e.g., +1 415 555 2671 or +44 20 7946 0912)."

    @staticmethod
    def validate_url(url: str) -> tuple[bool, str]:
        """
        Validates URL syntax and performs a live HEAD ping to check if the site is reachable.
        """
        if not url or not isinstance(url, str):
            return False, "Please enter a valid website URL (e.g., https://yourcompany.com)."

        url = url.strip()

        # Prepend https if user enters "mycompany.com"
        if not (url.startswith("http://") or url.startswith("https://")):
            url = "https://" + url

        # Syntax Check
        url_pattern = r"^https?://(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}(?::\d+)?(?:/[^\s]*)?$"
        if not re.match(url_pattern, url):
            return False, "The URL format is invalid. Please enter a proper web address (e.g., https://yourcompany.com)."

        # Live Ping / Server Response Check
        try:
            req = urllib.request.Request(
                url,
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'},
                method='HEAD'
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status in [200, 301, 302, 307, 308]:
                    return True, url
        except Exception:
            return False, "We couldn't reach that website. Please check the domain for typos and make sure it is live online."

        return True, url


if __name__ == "__main__":
    # Quick manual smoke test: python -m validation.input_validator
    print(InputValidator.validate_email("info@bmymarketer.com"))
    print(InputValidator.validate_phone("+1 415 555 2671"))
    print(InputValidator.validate_url("bmymarketer.com"))
