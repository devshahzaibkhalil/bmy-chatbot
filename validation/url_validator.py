"""
validation/url_validator.py
Lightweight, dependency-free website URL validation used across the BMY
Marketer chatbot (guided purchase flow's "website" question), mirroring the
style of email_validator.py and phone_validator.py.

Checks performed, in order:
  1. Presence / length
  2. Format (domain + valid TLD, optional scheme/path/query)
  3. Normalization - adds "https://" if the visitor typed a bare domain
     (e.g. "example.com" -> "https://example.com") so what gets stored is
     always a usable, clickable link
"""

import re

# Accepts, case-insensitively:
#   example.com
#   www.example.com
#   http://example.com/path?query=1
#   https://sub.example.co.uk
# Requires a real-looking domain (label.label...tld) - rejects things like
# "not a url", "asdkjaslkdj", or a bare word with no dot.
URL_REGEX = re.compile(
    r"^(?:https?://)?"                              # optional scheme
    r"(?:www\.)?"                                    # optional www.
    r"(?P<host>[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"    # first domain label
    r"(?:\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+)"     # one or more further labels (requires a dot + TLD)
    r"(?::\d{1,5})?"                                  # optional port
    r"(?:[/?#]\S*)?$",                                # optional path/query/fragment
    re.IGNORECASE,
)


def validate_url(url):
    """
    Returns:
        {
            "valid": bool,
            "message": str,
            "url": str
        }
    On success, "url" is normalized to always include a "https://" scheme.
    """
    if not url:
        return {
            "valid": False,
            "message": "Please share your website URL (e.g. www.example.com).",
            "url": "",
        }

    url = url.strip()

    if len(url) > 2048:
        return {
            "valid": False,
            "message": "That URL looks too long - please double-check it.",
            "url": url,
        }

    match = URL_REGEX.match(url)
    if not match:
        return {
            "valid": False,
            "message": (
                "That doesn't look like a valid website URL - please share "
                "it in a format like www.example.com or https://example.com."
            ),
            "url": url,
        }

    host = match.group("host")
    # Require at least one dot with a plausible TLD (2+ letters) so single
    # words that happen to pass the label regex (unlikely given the pattern
    # above, but kept as a defensive check) aren't accepted as domains.
    if "." not in host or not re.search(r"\.[a-zA-Z]{2,}$", host):
        return {
            "valid": False,
            "message": (
                "That doesn't look like a valid website URL - please share "
                "it in a format like www.example.com or https://example.com."
            ),
            "url": url,
        }

    if re.match(r"^https?://", url, re.IGNORECASE):
        normalized = url
    else:
        normalized = f"https://{url}"

    return {
        "valid": True,
        "message": "",
        "url": normalized,
    }
