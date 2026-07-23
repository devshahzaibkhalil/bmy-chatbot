"""
chat_engine.py
Local, offline NLP matching engine for the BMY Marketer AI Assistant.

No external AI API is used. Matching is done with fuzzywuzzy (Levenshtein-
based string similarity) against a local FAQ + knowledge base, plus a small
set of rule-based intent detectors for greetings, contact requests, pricing,
services, and lead-capture signals (email/phone/budget mentions).
"""

import json
import os
import re

from fuzzywuzzy import fuzz, process

from config import Config
from validation.email_validator import validate_email
from validation.phone_validator import validate_phone
from validation.url_validator import validate_url

_GREETING_PATTERNS = [
    "hi", "hello", "hey", "good morning", "good afternoon", "good evening",
    "salam", "assalam", "yo", "greetings",
]

_THANKS_PATTERNS = ["thank you", "thanks", "thankyou", "appreciate it"]

_CONTACT_PATTERNS = [
    "contact", "phone number", "email address", "call you", "reach you",
    "get in touch", "talk to someone", "talk to a human", "speak to agent",
]

# Generic filler words that carry no real topic meaning on their own. Used
# to stop short generic phrases (e.g. "how its work") from false-matching
# unrelated FAQs purely because both share words like "how" and "work" -
# fuzzy scoring alone rates that highly even with zero actual topic overlap.
_STOPWORDS = {
    "how", "what", "why", "when", "where", "who", "which", "whom",
    "is", "are", "was", "were", "do", "does", "did", "can", "could",
    "will", "would", "should", "the", "a", "an", "to", "of", "for",
    "and", "or", "in", "on", "at", "this", "that", "it", "its", "i",
    "you", "your", "my", "me", "we", "us", "with", "about",
    "work", "works", "working", "get", "give", "need", "want",
}


def _content_tokens(text):
    """Words in `text` that aren't generic filler - i.e. carry real topic meaning."""
    return {w for w in re.findall(r"[a-z]+", text.lower()) if w not in _STOPWORDS and len(w) > 1}


# Filler words to strip before fuzzy-matching a service name. Superset of
# _STOPWORDS - "service"/"services" and similar request-framing words are
# fine to keep in an FAQ query (they don't distort token_set_ratio against
# a full sentence), but against a short 1-3 word service name they dilute
# the score badly. E.g. "i want graphics desiging services" scores 60
# against "graphic design" (below the >=80 bar) purely because of "i",
# "want" and "services" padding the token-set diff; stripped down to
# "graphics desiging" it scores 90.
_SERVICE_MATCH_FILLER = _STOPWORDS | {
    "service", "services", "please", "interested", "looking", "some", "any",
}


def _strip_service_filler(text):
    tokens = [w for w in re.findall(r"[a-z]+", text.lower()) if w not in _SERVICE_MATCH_FILLER and len(w) > 1]
    return " ".join(tokens) if tokens else text.lower()


def _content_overlap(tokens_a, tokens_b):
    """
    True if the two content-token sets share a real topic word. Uses a light
    prefix check (not just exact equality) so word-form differences like
    "graphic"/"graphics" or "design"/"designing" still count as related -
    only exact-filler-word overlap (already excluded from these sets) is
    rejected, not legitimate stems of the same word.
    """
    for a in tokens_a:
        for b in tokens_b:
            if a == b:
                return True
            prefix_len = min(len(a), len(b), 5)
            if prefix_len >= 4 and a[:prefix_len] == b[:prefix_len]:
                return True
    return False


_PRICING_PATTERNS = ["price", "pricing", "cost", "how much", "plan", "package", "budget"]

# Generic words that are ambiguous across more than one BMyMarketer service
# (SEO, GBP, and AI visibility are all forms of "optimization"). See the
# guard in respond() that uses this.
_AMBIGUOUS_SERVICE_WORDS = {"optimization", "optimisation"}

# Direct alias -> service name map, checked on word boundaries before fuzzy
# scoring. Needed because abbreviations like "gbp" don't fuzzy-match well
# against a spelled-out service name, and because "Search Engine
# Optimization (SEO)" and "Google Business Profile Optimization" both
# contain the word "optimization" and can otherwise tie in fuzzy scoring.
_SERVICE_ALIAS_TO_NAME = {
    "gbp": "google business profile optimization",
    "gmb": "google business profile optimization",
    "google business profile": "google business profile optimization",
    "google my business": "google business profile optimization",
    "ui/ux": "graphic design",
    "ui ux": "graphic design",
    "uiux": "graphic design",
    "ui design": "graphic design",
    "ux design": "graphic design",
    "ui": "graphic design",
    "ux": "graphic design",
    "aeo": "ai visibility optimization (aio, aeo & geo)",
    "aio": "ai visibility optimization (aio, aeo & geo)",
    "geo": "ai visibility optimization (aio, aeo & geo)",
    "answer engine optimization": "ai visibility optimization (aio, aeo & geo)",
    "generative engine optimization": "ai visibility optimization (aio, aeo & geo)",
    "ai visibility": "ai visibility optimization (aio, aeo & geo)",
    "ai optimization": "ai visibility optimization (aio, aeo & geo)",
    "ai powered seo": "ai visibility optimization (aio, aeo & geo)",
}

# Direct alias -> pricing category map. Checked with word-boundary matching
# BEFORE fuzzy scoring, because fuzzy token_set_ratio against a long,
# multi-word category name like "AI-Powered SEO, AIO, AEO and GEO" gets
# diluted by unmatched words and never clears the match threshold even
# when the user's abbreviation (e.g. "aeo") is an exact, unambiguous hit.
# Keys are checked longest-first so specific phrases win over short
# substrings (e.g. "ai powered seo" is checked before plain "seo").
_PRICING_ALIAS_TO_CATEGORY = {
    "google business profile": "Google Business Profile",
    "google my business": "Google Business Profile",
    "gbp": "Google Business Profile",
    "gmb": "Google Business Profile",
    "answer engine optimization": "AI-Powered SEO, AIO, AEO and GEO",
    "generative engine optimization": "AI-Powered SEO, AIO, AEO and GEO",
    "ai powered seo": "AI-Powered SEO, AIO, AEO and GEO",
    "ai optimization": "AI-Powered SEO, AIO, AEO and GEO",
    "ai seo": "AI-Powered SEO, AIO, AEO and GEO",
    "aeo": "AI-Powered SEO, AIO, AEO and GEO",
    "geo": "AI-Powered SEO, AIO, AEO and GEO",
    "aio": "AI-Powered SEO, AIO, AEO and GEO",
    "search engine optimization": "SEO",
    "seo": "SEO",
    "social media management": "Social Media Management",
    "social media": "Social Media Management",
    "smm": "Social Media Management",
    "performance marketing": "Performance Marketing",
    "paid ads": "Performance Marketing",
    "ppc": "Performance Marketing",
    "graphic design": "Graphic Design",
    "logo design": "Graphic Design",
    "video editing": "Video Editing",
    "website development": "Website Development",
    "web development": "Website Development",
    "web design": "Website Development",
}

_SCHEDULING_PATTERNS = [
    "book a call", "schedule a call", "book a consultation", "schedule a consultation",
    "book an appointment", "schedule an appointment", "set up a call",
    "can we schedule", "can we set up a time", "book a meeting", "schedule a meeting",
]

# Phrases that should start the SHORT specialist/consultation contact flow
# (just full name -> email -> phone -> goals, one at a time) rather than the
# full 15-question purchase/qualification flow below, and rather than
# falling through to a single static FAQ answer that asks for all of those
# in one message (see faq_200 in knowledge/faqs/general.json - that reply
# alone can't actually collect anything since a plain FAQ answer has no
# memory of the conversation; the very next thing the visitor types, e.g.
# just their name, then fails to match any FAQ and hits the fallback
# message instead of being recognized as "answering the name question").
_SPECIALIST_PATTERNS = [
    "speak with a specialist", "speak to a specialist", "talk to a specialist",
    "connect me with a specialist", "connect with a specialist",
    "speak with an expert", "speak to an expert", "talk to an expert",
    "speak with expert", "talk to expert",
    "book a consultation", "book consultation", "schedule a consultation",
    "schedule consultation", "i need a consultation", "free consultation",
    "i want a consultation",
]

_PURCHASE_PATTERNS = [
    "purchase", "purchased", "buy", "checkout", "check out", "place an order",
    "place order", "make a payment", "get billed", "how do i pay",
    "how can i pay", "send me an invoice", "get an invoice", "invoice",
    "ready to proceed", "ready to start", "sign me up", "sign up",
    "want to buy", "want to purchase", "how do i purchase", "how do i buy",
]

# Short, low-content acknowledgments. Only treated as a "flow closing"
# sign-off (see respond()'s known_contact block / _is_flow_closing_message)
# when the visitor's *previous* bot message was one of the guided flow's
# own closing messages - anywhere else in the conversation these fall
# through to normal handling as usual, since a bare "ok" mid-FAQ shouldn't
# trigger a thank-you message.
_CLOSING_ACK_PATTERNS = [
    "ok", "okay", "k", "kk", "alright", "all right", "sounds good",
    "great", "perfect", "cool", "got it", "awesome", "nice", "thanks",
    "thank you", "thankyou", "thx", "ty", "understood", "noted",
]

# "How does that work?" style follow-ups asked right after the bot has
# named/described a service (service_info intent, or a pick from the
# services overview list). When these fire and a service is already on
# record for the conversation (interested_service), we explain that
# service and ask a Yes/No to proceed, rather than falling through to the
# generic "I'm not sure I understood that" fallback.
_HOW_IT_WORKS_PATTERNS = [
    "how will you do that", "how do you do that", "how will you do this", "how do you do this",
    "how can you do that", "how can you do this", "how can we do that", "how can we do this",
    "how would you do that", "how would you do this", "how would we do that", "how would we do this",
    "how does that work", "how does this work", "how will that work", "how will this work",
    "how do you provide that", "how do you provide this", "how is that done", "how is this done",
    "explain how", "explain that", "explain this", "how do you handle that", "how do you handle this",
    "what does that involve", "what does this involve", "tell me more about that", "tell me more about this",
]

# Phrases asking for a full rundown of everything BMyMarketer offers, as
# opposed to a question about one specific service (handled separately by
# _match_service). Checked before the fuzzy service/FAQ matching so a broad
# "what do you do" doesn't get swallowed by a single service's description.
_SERVICE_LIST_PATTERNS = [
    "what services do you offer", "what are your services",
    "tell me your services", "tell me about your services",
    "what can you help me with", "what do you do",
    "what solutions do you provide", "service list", "services list",
    "show all services", "show me your services", "list your services",
    "list of services", "available services",
]

_SERVICES_OVERVIEW_REPLY = (
    "We offer a comprehensive range of digital marketing and business growth services:\n\n"
    "\U0001F310 Website Development\n"
    "\u2022 Custom Website Development\n"
    "\u2022 Front-End Development\n"
    "\u2022 Back-End Development\n"
    "\u2022 Full-Stack Development\n"
    "\u2022 WordPress Development\n"
    "\u2022 Website Maintenance\n"
    "\u2022 Website Security\n"
    "\u2022 Website Speed Optimization\n\n"
    "\U0001F50D Search Engine Optimization (SEO)\n\n"
    "\U0001F4CD Google Business Profile Optimization\n\n"
    "\U0001F916 AI Visibility Optimization (AIO, AEO & GEO)\n\n"
    "\U0001F3A8 Graphic Design\n"
    "\u2022 Logo Design\n"
    "\u2022 Brand Identity Design\n"
    "\u2022 Business Card Design\n"
    "\u2022 Flyer Design\n"
    "\u2022 Brochure Design\n"
    "\u2022 Banner Design\n"
    "\u2022 Social Media Post Design\n"
    "\u2022 Infographic Design\n\n"
    "\U0001F3AC Video Editing\n\n"
    "\U0001F4F1 Social Media Marketing\n\n"
    "\u2709\uFE0F Email Marketing\n\n"
    "\U0001F916 HappyAssist AI Answering Service\n\n"
    "If you'd like more information about any specific service, I'd be happy to "
    "explain it in detail or help you choose the best option for your business."
)

_EMAIL_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
_PHONE_REGEX = re.compile(r"(\+?\d[\d\-\s()]{7,}\d)")


class ChatEngine:
    def __init__(self):
        with open(Config.WEBSITE_DATA_PATH, "r", encoding="utf-8") as f:
            self.website_data = json.load(f)
        self.faqs = self._load_faqs(Config.FAQ_DATA_DIR)
        with open(Config.PURCHASE_FLOW_PATH, "r", encoding="utf-8") as f:
            purchase_flow = json.load(f)
        with open(Config.SERVICE_FAQS_PATH, "r", encoding="utf-8") as f:
            self.service_faqs = json.load(f)

        self._service_lookup = {}
        for service in self.website_data.get("services", []):
            self._service_lookup[service["name"].lower()] = service

        # Flatten the staged question list from purchase_flow.json into one
        # ordered sequence, remembering which stage each question belongs to
        # so the flow can announce "Qualification stage" etc. the first time
        # it steps into a new one.
        self._flow_intro = purchase_flow.get("intro", "Happy to get you started!")
        self._flow_questions = []
        for stage in purchase_flow.get("stages", []):
            for i, question in enumerate(stage.get("questions", [])):
                self._flow_questions.append({
                    "key": question["key"],
                    "prompt": question["prompt"],
                    "options": question.get("options"),
                    "stage_name": stage.get("name"),
                    "is_first_in_stage": i == 0,
                })

        # Short 4-question contact flow for "speak with a specialist" /
        # consultation requests - deliberately separate from the full
        # _flow_questions above (that one asks about service, goals,
        # budget, timeline, etc. too, which is a lot more than someone
        # clicking "Speak With a Specialist" is asking for). No `options`
        # on any of these - they're all free-text answers.
        self._specialist_flow_questions = [
            {"key": "full_name", "prompt": "Let's start with your full name."},
            {"key": "email", "prompt": "Could you please provide your email address?"},
            {"key": "phone", "prompt": "Could you now share your phone number, including the country code if applicable?"},
            {"key": "goals", "prompt": "Finally, could you briefly describe your project or business goals?"},
        ]

    # ---------- Public API ----------

    @staticmethod
    def _load_faqs(faq_dir):
        """
        Load and merge every category file under knowledge/faqs/ (e.g.
        seo.json, pricing.json, comparisons.json) into one flat list, in
        the same shape the old single knowledge/faq.json used to provide.
        Matching code elsewhere (_best_faq_match, etc.) just iterates this
        flat list and doesn't care which file an entry came from - the
        split is purely for keeping the knowledge base organized/editable
        by category, not a change to matching behavior.
        """
        faqs = []
        for name in sorted(os.listdir(faq_dir)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(faq_dir, name)
            with open(path, "r", encoding="utf-8") as f:
                faqs.extend(json.load(f))
        return faqs

    def get_welcome_message(self):
        return self.website_data["greetings"]["welcome_message"]


    def extract_contact_info(self, text):
        """Pull email/phone out of free text so we can save leads automatically."""
        email_match = _EMAIL_REGEX.search(text)
        phone_match = _PHONE_REGEX.search(text)
        return {
            "email": email_match.group(0) if email_match else None,
            "phone": phone_match.group(0).strip() if phone_match else None,
        }

    # ---------- Public API: guided purchase / qualification flow ----------
    #
    # Walks the visitor through the 15-question, 3-stage intake (see
    # knowledge/purchase_flow.json) one question at a time instead of
    # dumping a single "here's how to buy" paragraph on them. Progress is
    # tracked by the caller (app.py persists lead_flow_step/lead_flow_answers
    # on the conversation row, the same way it already persists
    # pending_pricing_topic) and handed back in on every call.

    _FLOW_CANCEL_WORDS = {"cancel", "stop", "never mind", "nevermind", "quit", "exit"}

    # Shown right after the visitor answers the budget question, before the
    # flow moves on to the remaining decision-maker / contact-info questions.
    _BUDGET_THANK_YOU = (
        "Thank you for sharing your information. A member of our team will "
        "review your requirements and recommend the best strategy for your "
        "business.\n\n"
        "Thank you for completing this part of your consultation with B My "
        "Marketer. We've successfully received your project details, "
        "including your selected service(s), business goals, preferred "
        "timeline, and budget. Our team will carefully review your "
        "requirements and develop a customized strategy tailored to your "
        "business objectives. One of our specialists will contact you "
        "shortly to discuss your project, answer any questions you may "
        "have, and guide you through the next steps.\n\n"
        "Thank you for choosing B My Marketer \u2014 we appreciate the "
        "opportunity to work with you. Just a couple more quick questions "
        "so we can get you connected with the right person."
    )

    # Message shown when the visitor says they don't have a website yet -
    # reassures them a website isn't a prerequisite and moves straight on
    # to the location question instead of asking for a URL.
    _NO_WEBSITE_REPLY = (
        "No problem. A website isn't required to get started, and we can "
        "still help improve your online visibility."
    )

    # Shown when the visitor says "yes" to having a website but hasn't
    # given us the URL yet - asks for it once. If they still don't provide
    # it on the next turn we stop asking and move on (see _advance_lead_flow).
    _ASK_WEBSITE_URL = (
        "Great! Please share your website URL so we can better understand "
        "your current online presence."
    )

    _WEBSITE_URL_REGEX = re.compile(
        r"(https?://[^\s]+|www\.[^\s]+|\b[a-z0-9-]+\.(?:com|net|org|io|co|biz|info|us)(?:/[^\s]*)?\b)",
        re.IGNORECASE,
    )
    _YES_WORDS = {"yes", "yeah", "yep", "yup", "sure", "correct", "affirmative"}
    _NO_WORDS = {"no", "nope", "nah", "not yet", "don't have one", "dont have one", "none"}

    # Free-text ways a visitor might say "I don't have any particular
    # challenge" when asked about their biggest issue - answered with a
    # reassuring note instead of just filing "nothing" away silently.
    _NO_ISSUE_WORDS = {
        "nothing", "none", "n/a", "na", "no issue", "no issues",
        "not really", "no problem", "no problems", "nope",
    }
    _NO_ISSUE_REPLY = (
        "That's great to hear! Even if you're not facing any major "
        "challenges, we can still help identify opportunities to improve "
        "your online visibility and drive additional growth."
    )

    # Free-text ways a visitor might say they don't have a company name yet
    # when asked for their business name. First occurrence gets a gentle
    # nudge toward a working/idea name; if they still don't give one on the
    # next turn, the second message points them at "Skip" so the flow isn't
    # stuck asking forever.
    _NO_COMPANY_WORDS = {"n/a", "na", "none", "no", "nil", "not applicable"}
    _NO_COMPANY_SUBSTRINGS = (
        "don't have", "dont have", "no company", "not decided",
        "haven't decided", "havent decided", "not sure yet",
        "no business", "not registered",
    )
    _BUSINESS_NAME_NO_COMPANY_REPLY = (
        "No problem. If you don't have a registered company name yet, you "
        "can simply enter your business idea or the name you plan to use."
    )
    _BUSINESS_NAME_FOLLOWUP_REPLY = (
        "That's perfectly fine. Please share the name you plan to use for "
        "your business, or type \"Skip\" if you haven't decided yet."
    )
    _BUSINESS_NAME_SKIP_REPLY = (
        "No problem. We'll leave the company name blank for now and "
        "continue with the consultation."
    )

    def _is_no_company_answer(self, text):
        lower = text.strip().lower()
        if lower in self._NO_COMPANY_WORDS:
            return True
        return any(s in lower for s in self._NO_COMPANY_SUBSTRINGS)

    def _match_option(self, raw_text, options):
        """Fuzzy-match free text against a question's option labels.
        Returns the matched label, or None if nothing scores highly enough
        to be confident it's a real selection rather than an off-topic or
        malformed answer (e.g. someone typing "Yes" for a timeline
        question, which shouldn't silently become a date)."""
        if not options:
            return None
        labels = [o["label"] for o in options]
        best_label, best_score = process.extractOne(
            raw_text.lower(), [l.lower() for l in labels], scorer=fuzz.token_set_ratio
        ) if labels else (None, 0)
        if best_score >= 80:
            for l in labels:
                if l.lower() == best_label:
                    return l
        return None

    def _flow_question_text(self, index):
        q = self._flow_questions[index]
        if q["is_first_in_stage"] and q.get("stage_name"):
            return f"{q['stage_name']}\n\n{q['prompt']}"
        return q["prompt"]

    @staticmethod
    def _next_unanswered_step(questions, answers, start):
        """Returns the index of the first question in `questions` at or
        after `start` whose key isn't already present (truthy) in
        `answers`. Used so a visitor who already gave us their name/email/
        phone earlier in this conversation (see `known_contact` on
        respond()) isn't asked for it again when a later guided flow
        reaches that question. Returns len(questions) if everything
        remaining is already answered."""
        i = start
        while i < len(questions) and answers.get(questions[i]["key"]):
            i += 1
        return i

    def _start_specialist_flow(self, known_contact=None):
        answers = {"_flow": "specialist"}
        answers.update({k: v for k, v in (known_contact or {}).items() if v})
        start_index = self._next_unanswered_step(self._specialist_flow_questions, answers, 0)
        if start_index >= len(self._specialist_flow_questions):
            # Every question this flow would ask is already known (rare -
            # only "goals" wasn't pre-fillable) - fall back to asking from
            # the top rather than returning an empty/invalid flow.
            start_index = 0
        first = self._specialist_flow_questions[start_index]
        intro = (
            "I'd be happy to arrange a free consultation for you."
            if start_index == 0 else
            "I'd be happy to arrange a free consultation for you - I already "
            "have some of your details on file, so I just need a bit more."
        )
        return {
            "answer": f"{intro}\n\n{first['prompt']}",
            "matched_faq_id": None,
            "match_score": 100,
            "intent": "specialist_flow_start",
            "needs_escalation": False,
            "pending_pricing_topic": "",
            "options": None,
            "lead_flow_step": start_index,
            # The "_flow" marker is how respond()/_advance_lead_flow tell
            # this short flow apart from the full purchase flow when the
            # conversation resumes on the next message - both flows share
            # the same conversations.lead_flow_step / lead_flow_answers
            # columns, so the answers dict itself carries which flow is
            # active.
            "lead_flow_answers": answers,
            "lead_flow_complete": False,
            "lead_flow_data": None,
        }

    def _advance_specialist_flow(self, raw_text, step, answers):
        answers = dict(answers or {})
        stripped = raw_text.strip()

        if stripped.lower() in self._FLOW_CANCEL_WORDS:
            return {
                "answer": (
                    "No problem, I've cancelled that. Let me know whenever you'd "
                    "like to pick it back up, or ask me anything else in the meantime."
                ),
                "matched_faq_id": None,
                "match_score": 100,
                "intent": "lead_flow_cancelled",
                "needs_escalation": False,
                "pending_pricing_topic": "",
                "options": None,
                "lead_flow_step": None,
                "lead_flow_answers": None,
                "lead_flow_complete": False,
                "lead_flow_data": None,
            }

        current = self._specialist_flow_questions[step]
        key = current["key"]

        def _reask(message):
            return {
                "answer": message,
                "matched_faq_id": None,
                "match_score": 100,
                "intent": "lead_flow_progress",
                "needs_escalation": False,
                "pending_pricing_topic": "",
                "options": None,
                "lead_flow_step": step,
                "lead_flow_answers": answers,
                "lead_flow_complete": False,
                "lead_flow_data": None,
            }

        if key == "full_name":
            if not stripped:
                return _reask("Could you please tell me your full name?")
            answers["full_name"] = stripped.title()
            ack = f"Thank you, {answers['full_name']}."
        elif key == "email":
            result = validate_email(stripped)
            if not result["valid"]:
                return _reask(result["message"])
            answers["email"] = result["email"]
            ack = "Thank you! I've recorded your email address."
        elif key == "phone":
            result = validate_phone(stripped)
            if not result["valid"]:
                return _reask(result["message"])
            answers["phone"] = result["phone"]
            ack = "Perfect! I've recorded your phone number."
        else:  # "goals"
            if not stripped:
                return _reask("Could you briefly describe your project or business goals?")
            answers["goals"] = stripped
            ack = "Thank you for sharing your goals."

        next_step = self._next_unanswered_step(self._specialist_flow_questions, answers, step + 1)
        if next_step < len(self._specialist_flow_questions):
            next_q = self._specialist_flow_questions[next_step]
            return {
                "answer": f"{ack}\n\n{next_q['prompt']}",
                "matched_faq_id": None,
                "match_score": 100,
                "intent": "lead_flow_progress",
                "needs_escalation": False,
                "pending_pricing_topic": "",
                "options": None,
                "lead_flow_step": next_step,
                "lead_flow_answers": answers,
                "lead_flow_complete": False,
                "lead_flow_data": None,
            }

        closing = (
            f"{ack}\n\nYour consultation request has been successfully submitted. "
            "A member of our team will contact you shortly to discuss your "
            "project and the next steps."
        )
        return {
            "answer": closing,
            "matched_faq_id": None,
            "match_score": 100,
            "intent": "lead_flow_complete",
            "needs_escalation": False,
            "pending_pricing_topic": "",
            "options": None,
            "lead_flow_step": None,
            "lead_flow_answers": None,
            "lead_flow_complete": True,
            "lead_flow_data": answers,
        }

    def _start_lead_flow(self, known_contact=None):
        answers = {k: v for k, v in (known_contact or {}).items() if v}
        start_index = self._next_unanswered_step(self._flow_questions, answers, 0)
        if start_index >= len(self._flow_questions):
            start_index = 0
        first = self._flow_questions[start_index]
        return {
            "answer": f"{self._flow_intro}\n\n{self._flow_question_text(start_index)}",
            "matched_faq_id": None,
            "match_score": 100,
            "intent": "lead_flow_start",
            "needs_escalation": False,
            "pending_pricing_topic": "",
            "options": first["options"],
            "lead_flow_step": start_index,
            "lead_flow_answers": answers,
            "lead_flow_complete": False,
            "lead_flow_data": None,
        }

    def _flow_advance_with_message(self, step, answers, prefix_message):
        """Move on from `step` to the next question, optionally prepending
        an acknowledgment message (e.g. "No problem, a website isn't
        required...") in front of the next prompt. Mirrors the tail end of
        _advance_lead_flow so the website/no-issue branches don't have to
        duplicate the stage-header / budget-recap / closing logic."""
        answers.pop("_awaiting_website_url", None)
        next_step = self._next_unanswered_step(self._flow_questions, answers, step + 1)

        if next_step < len(self._flow_questions):
            next_q = self._flow_questions[next_step]
            next_text = self._flow_question_text(next_step)
            if self._flow_questions[step]["key"] == "budget_range":
                next_text = f"{self._BUDGET_THANK_YOU}\n\n{next_text}"
            if prefix_message:
                next_text = f"{prefix_message}\n\n{next_text}"
            return {
                "answer": next_text,
                "matched_faq_id": None,
                "match_score": 100,
                "intent": "lead_flow_progress",
                "needs_escalation": False,
                "pending_pricing_topic": "",
                "options": next_q["options"],
                "lead_flow_step": next_step,
                "lead_flow_answers": answers,
                "lead_flow_complete": False,
                "lead_flow_data": None,
            }

        c = self.website_data["contact"]
        closing = (
            "That's everything I need \u2014 thank you! Here's what happens next:\n\n"
            "1) We review what you've shared and confirm the scope.\n"
            "2) We send a written proposal and invoice.\n"
            "3) A deposit secures your start date, and we begin on the agreed schedule.\n\n"
            f"A team member will follow up shortly (or call us anytime at {c['phone']} "
            "if you'd like to speed things along)."
        )
        if prefix_message:
            closing = f"{prefix_message}\n\n{closing}"
        return {
            "answer": closing,
            "matched_faq_id": None,
            "match_score": 100,
            "intent": "lead_flow_complete",
            "needs_escalation": False,
            "pending_pricing_topic": "",
            "options": None,
            "lead_flow_step": None,
            "lead_flow_answers": None,
            "lead_flow_complete": True,
            "lead_flow_data": answers,
        }

    def _advance_lead_flow(self, raw_text, step, answers):
        answers = dict(answers or {})

        if raw_text.strip().lower() in self._FLOW_CANCEL_WORDS:
            return {
                "answer": (
                    "No problem, I've cancelled that. Let me know whenever you'd "
                    "like to pick it back up, or ask me anything else in the meantime."
                ),
                "matched_faq_id": None,
                "match_score": 100,
                "intent": "lead_flow_cancelled",
                "needs_escalation": False,
                "pending_pricing_topic": "",
                "options": None,
                "lead_flow_step": None,
                "lead_flow_answers": None,
                "lead_flow_complete": False,
                "lead_flow_data": None,
            }

        current = self._flow_questions[step]
        stripped = raw_text.strip()

        # Before blindly filing this text into whatever field happens to be
        # active, check whether the visitor is actually naming/correcting a
        # service (e.g. typing "I need a GBP optimization" while we're
        # asking about their business goal). Without this check, that text
        # gets silently saved as their "goal" answer and the flow moves on -
        # the visitor's correction is lost entirely. Reuses _match_service's
        # existing alias/fuzzy logic (same >=80 confidence bar used
        # elsewhere) so a genuine answer to the current question isn't
        # misdetected as a service mention. Identity/contact fields are
        # excluded - a business literally named "Google", or an email/phone
        # that happens to fuzzy-match a service name, is a real answer, not
        # a correction.
        _SERVICE_CORRECTION_EXCLUDED_KEYS = {
            "business_name", "full_name", "email", "phone",
        }
        service_hit = (
            self._match_service(stripped.lower())
            if stripped and current["key"] not in _SERVICE_CORRECTION_EXCLUDED_KEYS
            else None
        )

        if current["key"] != "service" and service_hit:
            answers["service"] = service_hit["name"]
            ack = f"Got it \u2014 updated your service to {service_hit['name']}."
            return {
                "answer": f"{ack}\n\n{current['prompt']}",
                "matched_faq_id": None,
                "match_score": 100,
                "intent": "lead_flow_service_correction",
                "needs_escalation": False,
                "pending_pricing_topic": "",
                "options": current["options"],
                "lead_flow_step": step,
                "lead_flow_answers": answers,
                "lead_flow_complete": False,
                "lead_flow_data": None,
                "selected_service": service_hit["name"],
            }

        if current["key"] == "service" and service_hit:
            # Normalize to the canonical service name (e.g. "gbp" ->
            # "Google Business Profile Optimization") instead of storing
            # whatever raw text the visitor typed, same as every other
            # service-matching path in this file already does.
            answers["service"] = service_hit["name"]
        elif current["key"] == "website":
            lower = stripped.lower()

            if answers.get("_awaiting_website_url"):
                # Second turn: they said "Yes" and we specifically asked for
                # the URL - validate it properly instead of just checking it
                # loosely looks URL-shaped.
                result = validate_url(stripped)
                if result["valid"]:
                    answers.pop("_awaiting_website_url", None)
                    answers["website"] = result["url"]
                    ack = f"Thank you. I've noted your website:\n{result['url']}"
                    return self._flow_advance_with_message(step, answers, ack)

                if answers.get("_website_retry"):
                    # Already gave one invalid attempt - don't loop forever,
                    # just move on without a website.
                    answers.pop("_awaiting_website_url", None)
                    answers.pop("_website_retry", None)
                    answers["website"] = ""
                    return self._flow_advance_with_message(step, answers, None)

                answers["_website_retry"] = True
                return {
                    "answer": result["message"],
                    "matched_faq_id": None,
                    "match_score": 100,
                    "intent": "lead_flow_progress",
                    "needs_escalation": False,
                    "pending_pricing_topic": "",
                    "options": None,
                    "lead_flow_step": step,
                    "lead_flow_answers": answers,
                    "lead_flow_complete": False,
                    "lead_flow_data": None,
                }

            # Not yet in the "waiting on the URL specifically" state - a
            # visitor can still paste their URL directly here, skipping the
            # Yes/No question entirely.
            direct_attempt = self._WEBSITE_URL_REGEX.search(stripped)
            if direct_attempt:
                result = validate_url(direct_attempt.group(0))
                if result["valid"]:
                    answers["website"] = result["url"]
                    ack = f"Thank you. I've noted your website:\n{result['url']}"
                    return self._flow_advance_with_message(step, answers, ack)

            if lower in self._NO_WORDS or self._match_option(lower, current["options"]) == "No":
                answers["website"] = ""
                return self._flow_advance_with_message(step, answers, self._NO_WEBSITE_REPLY)

            if lower in self._YES_WORDS or self._match_option(lower, current["options"]) == "Yes":
                # Ask for the URL, but stay on this same step - remember
                # we're now waiting on the URL specifically.
                answers["_awaiting_website_url"] = True
                return {
                    "answer": self._ASK_WEBSITE_URL,
                    "matched_faq_id": None,
                    "match_score": 100,
                    "intent": "lead_flow_progress",
                    "needs_escalation": False,
                    "pending_pricing_topic": "",
                    "options": None,
                    "lead_flow_step": step,
                    "lead_flow_answers": answers,
                    "lead_flow_complete": False,
                    "lead_flow_data": None,
                }

            # Anything else (didn't clearly say yes/no/URL) - re-ask once.
            return {
                "answer": (
                    "Just to confirm - do you currently have a business "
                    "website? Please choose Yes or No."
                ),
                "matched_faq_id": None,
                "match_score": 100,
                "intent": "lead_flow_progress",
                "needs_escalation": False,
                "pending_pricing_topic": "",
                "options": current["options"],
                "lead_flow_step": step,
                "lead_flow_answers": answers,
                "lead_flow_complete": False,
                "lead_flow_data": None,
            }
        elif current["key"] == "start_timing" and current.get("options"):
            matched = self._match_option(stripped, current["options"])
            if not matched:
                # Reject anything that isn't clearly one of the timeline
                # options (e.g. "Yes") instead of silently saving it.
                return {
                    "answer": (
                        "Could you please specify your preferred timeline?\n\n"
                        "Choose one of the following:"
                    ),
                    "matched_faq_id": None,
                    "match_score": 100,
                    "intent": "lead_flow_progress",
                    "needs_escalation": False,
                    "pending_pricing_topic": "",
                    "options": current["options"],
                    "lead_flow_step": step,
                    "lead_flow_answers": answers,
                    "lead_flow_complete": False,
                    "lead_flow_data": None,
                }
            answers["start_timing"] = matched
        elif current["key"] == "email":
            result = validate_email(stripped)
            if not result["valid"]:
                return {
                    "answer": result["message"],
                    "matched_faq_id": None,
                    "match_score": 100,
                    "intent": "lead_flow_progress",
                    "needs_escalation": False,
                    "pending_pricing_topic": "",
                    "options": None,
                    "lead_flow_step": step,
                    "lead_flow_answers": answers,
                    "lead_flow_complete": False,
                    "lead_flow_data": None,
                }
            answers["email"] = result["email"]
        elif current["key"] == "phone":
            result = validate_phone(stripped)
            if not result["valid"]:
                return {
                    "answer": result["message"],
                    "matched_faq_id": None,
                    "match_score": 100,
                    "intent": "lead_flow_progress",
                    "needs_escalation": False,
                    "pending_pricing_topic": "",
                    "options": None,
                    "lead_flow_step": step,
                    "lead_flow_answers": answers,
                    "lead_flow_complete": False,
                    "lead_flow_data": None,
                }
            answers["phone"] = result["phone"]
        elif current["key"] == "business_name":
            lower = stripped.lower()

            if lower == "skip":
                answers["business_name"] = ""
                answers.pop("_awaiting_business_name", None)
                return self._flow_advance_with_message(step, answers, self._BUSINESS_NAME_SKIP_REPLY)

            if self._is_no_company_answer(stripped):
                if answers.get("_awaiting_business_name"):
                    # Already nudged once and still no name/skip - point at
                    # Skip explicitly instead of asking a third time.
                    return {
                        "answer": self._BUSINESS_NAME_FOLLOWUP_REPLY,
                        "matched_faq_id": None,
                        "match_score": 100,
                        "intent": "lead_flow_progress",
                        "needs_escalation": False,
                        "pending_pricing_topic": "",
                        "options": None,
                        "lead_flow_step": step,
                        "lead_flow_answers": answers,
                        "lead_flow_complete": False,
                        "lead_flow_data": None,
                    }
                answers["_awaiting_business_name"] = True
                return {
                    "answer": self._BUSINESS_NAME_NO_COMPANY_REPLY,
                    "matched_faq_id": None,
                    "match_score": 100,
                    "intent": "lead_flow_progress",
                    "needs_escalation": False,
                    "pending_pricing_topic": "",
                    "options": None,
                    "lead_flow_step": step,
                    "lead_flow_answers": answers,
                    "lead_flow_complete": False,
                    "lead_flow_data": None,
                }

            # A real name (whether given right away or after the nudge above).
            answers.pop("_awaiting_business_name", None)
            answers["business_name"] = stripped
            ack = f"Thank you! I've recorded your company name as \"{stripped}.\""
            return self._flow_advance_with_message(step, answers, ack)
        elif current["key"] == "biggest_issue":
            answers["biggest_issue"] = stripped
            if stripped.lower() in self._NO_ISSUE_WORDS:
                return self._flow_advance_with_message(step, answers, self._NO_ISSUE_REPLY)
        else:
            answers[current["key"]] = stripped
        next_step = self._next_unanswered_step(self._flow_questions, answers, step + 1)

        if next_step < len(self._flow_questions):
            next_q = self._flow_questions[next_step]
            next_text = self._flow_question_text(next_step)

            # Once budget is captured, drop in a short interim "thank you"
            # recap before continuing on to the remaining decision-maker /
            # contact-info questions - the visitor gets a sense of closure
            # on the qualification portion even though a few questions
            # still remain.
            if current["key"] == "budget_range":
                next_text = f"{self._BUDGET_THANK_YOU}\n\n{next_text}"

            return {
                "answer": next_text,
                "matched_faq_id": None,
                "match_score": 100,
                "intent": "lead_flow_progress",
                "needs_escalation": False,
                "pending_pricing_topic": "",
                "options": next_q["options"],
                "lead_flow_step": next_step,
                "lead_flow_answers": answers,
                "lead_flow_complete": False,
                "lead_flow_data": None,
            }

        c = self.website_data["contact"]
        closing = (
            "That's everything I need \u2014 thank you! Here's what happens next:\n\n"
            "1) We review what you've shared and confirm the scope.\n"
            "2) We send a written proposal and invoice.\n"
            "3) A deposit secures your start date, and we begin on the agreed schedule.\n\n"
            f"A team member will follow up shortly (or call us anytime at {c['phone']} "
            "if you'd like to speed things along)."
        )
        return {
            "answer": closing,
            "matched_faq_id": None,
            "match_score": 100,
            "intent": "lead_flow_complete",
            "needs_escalation": False,
            "pending_pricing_topic": "",
            "options": None,
            "lead_flow_step": None,
            "lead_flow_answers": None,
            "lead_flow_complete": True,
            "lead_flow_data": answers,
        }

    # Substrings unique to the guided flows' own closing/completion
    # messages (see _advance_lead_flow, _flow_advance_with_message, and
    # _advance_specialist_flow) - used by _is_flow_closing_message below to
    # recognize when the visitor's next bare "okay" is a sign-off rather
    # than an answer to a question.
    _FLOW_CLOSING_MARKERS = (
        "That's everything I need",
        "Your consultation request has been successfully submitted",
        "I'll flag this to a member of our team",
        "A specialist will contact you shortly",
    )

    def _is_flow_closing_message(self, message):
        return any(marker in (message or "") for marker in self._FLOW_CLOSING_MARKERS)

    def _closing_thanks_reply(self):
        return {
            "answer": "Thanks for these details! Our team will reach out to you as soon as possible.",
            "matched_faq_id": None,
            "match_score": 100,
            "intent": "closing_thanks",
            "needs_escalation": False,
            "pending_pricing_topic": "",
            "options": None,
            "lead_flow_step": None,
            "lead_flow_answers": None,
            "lead_flow_complete": False,
            "lead_flow_data": None,
        }

    def respond(self, text, interested_service=None, pending_pricing_topic=None,
                lead_flow_step=None, lead_flow_answers=None, last_bot_message=None,
                known_contact=None):
        """
        Returns a dict:
          { answer, matched_faq_id, match_score, intent, needs_escalation,
            pending_pricing_topic, options, lead_flow_step, lead_flow_answers,
            lead_flow_complete, lead_flow_data }

        `lead_flow_step`/`lead_flow_answers`, when the caller has them (i.e.
        conversations.lead_flow_step is not NULL), mean the visitor is
        mid-way through the guided purchase/qualification flow - so this
        message is treated as the answer to the current question rather
        than run through normal intent matching. `lead_flow_data` is only
        populated (with the full answers dict) on the turn the flow
        completes, for the caller to build a lead record from.

        `last_bot_message`, when provided, is the text of the bot's
        previous message in this conversation - used only to resolve a
        bare reply (e.g. just "ABC") typed straight after one of the
        scripted flow_XXX FAQ prompts (see _best_faq_match).

        `known_contact`, when provided, is a dict of any of
        {"full_name", "email", "phone"} already captured for this visitor
        earlier in the conversation (e.g. from a previously completed
        guided flow) - see app.py's chat_message handler. Any guided flow
        that's about to start pre-fills these fields and skips straight
        past the corresponding questions instead of asking again.
        """
        text = text if text is not None else ""

        if lead_flow_step is not None:
            flow_marker = (lead_flow_answers or {}).get("_flow")
            if flow_marker == "specialist":
                return self._advance_specialist_flow(text, lead_flow_step, lead_flow_answers)
            if flow_marker == "service_faq":
                return self._advance_service_faq_flow(text, lead_flow_step, lead_flow_answers, known_contact)
            return self._advance_lead_flow(text, lead_flow_step, lead_flow_answers)

        clean = text.strip().lower()
        if not clean:
            result = self._fallback()
            result.setdefault("options", None)
            result.setdefault("lead_flow_step", None)
            result.setdefault("lead_flow_answers", None)
            result.setdefault("lead_flow_complete", False)
            result.setdefault("lead_flow_data", None)
            result.setdefault("selected_service", None)
            return result

        if self._matches_any(clean, _SPECIALIST_PATTERNS):
            return self._start_specialist_flow(known_contact)

        # Checked before the purchase-intent patterns below: phrases like
        # "I want to build or redesign a website" can fuzzy-match generic
        # purchase phrasing (e.g. "want to buy") on shared words alone, which
        # would hijack a service button into the wrong flow. A recognized
        # service should always win a tie over an incidental purchase-phrase
        # overlap.
        service_key = self._match_service_flow_trigger(clean)
        if service_key:
            return self._start_service_faq_flow(service_key, known_contact)

        if self._matches_any(clean, _PURCHASE_PATTERNS):
            return self._start_lead_flow(known_contact)

        # A short acknowledgment (e.g. "okay", "great", "thanks") typed
        # right after the guided purchase flow's own closing message gets
        # a brief, warm sign-off instead of falling through to generic FAQ
        # matching (which has nothing relevant to say to a bare "okay").
        if last_bot_message and self._is_flow_closing_message(last_bot_message) \
                and self._matches_any(clean, _CLOSING_ACK_PATTERNS):
            return self._closing_thanks_reply()

        result = self._respond_matching(text, interested_service=interested_service,
                                         pending_pricing_topic=pending_pricing_topic,
                                         last_bot_message=last_bot_message,
                                         known_contact=known_contact)
        result.setdefault("options", None)
        result.setdefault("lead_flow_step", None)
        result.setdefault("lead_flow_answers", None)
        result.setdefault("lead_flow_complete", False)
        result.setdefault("lead_flow_data", None)
        result.setdefault("selected_service", None)
        return result

    def _respond_matching(self, text, interested_service=None, pending_pricing_topic=None,
                           last_bot_message=None, known_contact=None):
        """
        Original rule-based + fuzzy matching pipeline (greetings, thanks,
        contact, pricing, scheduling, services, FAQ, documents, fallback).
        Purchase intent is now intercepted earlier by respond() and starts
        the guided flow instead of reaching the purchase-intent branch
        below, but that branch is left in place as a safety net in case
        _PURCHASE_PATTERNS ever matches on a path that bypasses respond().
        """
        clean = text.strip().lower()
        if not clean:
            return self._fallback()

        # Rule-based intents first (cheap, high precision)
        if self._is_greeting(clean):
            return {
                "answer": self.get_welcome_message(),
                "matched_faq_id": None,
                "match_score": 100,
                "intent": "greeting",
                "needs_escalation": False,
                "pending_pricing_topic": "",
            }

        if self._matches_any(clean, _THANKS_PATTERNS):
            return {
                "answer": "You're welcome! Let me know if there's anything else you'd like to know about BMyMarketer.",
                "matched_faq_id": None,
                "match_score": 100,
                "intent": "thanks",
                "needs_escalation": False,
                "pending_pricing_topic": "",
            }

        if self._matches_any(clean, _CONTACT_PATTERNS):
            c = self.website_data["contact"]
            answer = (
                f"You can reach BMyMarketer at {c['phone']} or {c['email']}. "
                f"Office hours: {c['office_hours']}. Would you like to leave your "
                f"name and email so our team can follow up directly?"
            )
            return {
                "answer": answer,
                "matched_faq_id": None,
                "match_score": 100,
                "intent": "contact_request",
                "needs_escalation": False,
                "pending_pricing_topic": "",
            }

        if self._matches_any(clean, _SERVICE_LIST_PATTERNS):
            return {
                "answer": _SERVICES_OVERVIEW_REPLY,
                "matched_faq_id": None,
                "match_score": 100,
                "intent": "service_list",
                "needs_escalation": False,
                "pending_pricing_topic": "",
            }

        # If we previously asked "which service's pricing?", try to resolve
        # a short follow-up (e.g. just "SEO" or "GBP") straight to that
        # category's pricing, even without the word "price" in it.
        if pending_pricing_topic:
            cat, cat_score = self._match_pricing_category(clean)
            if cat and cat_score >= 60:
                return {
                    "answer": self._category_price_answer(cat),
                    "matched_faq_id": None,
                    "match_score": cat_score,
                    "intent": "pricing",
                    "needs_escalation": False,
                    "pending_pricing_topic": "",
                }

        if self._matches_any(clean, _PRICING_PATTERNS):
            answer, resolved = self._pricing_answer(clean)
            return {
                "answer": answer,
                "matched_faq_id": None,
                "match_score": 100,
                "intent": "pricing" if resolved else "pricing_clarify",
                "needs_escalation": False,
                "pending_pricing_topic": "" if resolved else "awaiting",
            }

        # NOTE: service-flow triggers (knowledge/service_faqs.json) are
        # checked earlier in respond(), before this method is even called -
        # see the comment there for why that ordering matters.

        if self._matches_any(clean, _SCHEDULING_PATTERNS):
            return {
                "answer": (
                    "I'd be happy to help set that up! I've noted your request and someone "
                    "from our team will confirm the exact time with you shortly. In the "
                    "meantime, feel free to share your name and email so we can reach you."
                ),
                "matched_faq_id": None,
                "match_score": 100,
                "intent": "scheduling_request",
                "needs_escalation": False,
                "pending_pricing_topic": "",
            }

        if self._matches_any(clean, _PURCHASE_PATTERNS):
            return {
                "answer": self._purchase_answer(interested_service),
                "matched_faq_id": None,
                "match_score": 100,
                "intent": "purchase_intent",
                "needs_escalation": False,
                "pending_pricing_topic": "",
            }

        # A visitor who was just told about a service (either via
        # _match_service below or the services overview list) and asks
        # "how will you do that?" gets an explanation of THAT service, not
        # a generic "I can't understand" - plus a Yes/No prompt to move
        # straight into the guided intake for it.
        if interested_service and self._matches_any(clean, _HOW_IT_WORKS_PATTERNS):
            return self._explain_service_and_confirm(interested_service)

        # A plain "yes" right after that explanation confirms interest -
        # jump into the guided flow with the service already filled in from
        # what we remember about this conversation, instead of re-asking
        # "What service are you interested in?" from question one.
        if interested_service and clean in self._YES_WORDS:
            return self._start_lead_flow_with_service(interested_service, known_contact)
        if interested_service and clean in self._NO_WORDS:
            return {
                "answer": (
                    f"No problem at all! Let me know if you'd like more details on "
                    f"{interested_service}, or ask about any of our other services anytime."
                ),
                "matched_faq_id": None,
                "match_score": 100,
                "intent": "service_confirm_declined",
                "needs_escalation": False,
                "pending_pricing_topic": "",
            }

        # "Optimization" alone is genuinely ambiguous - we offer SEO, Google
        # Business Profile optimization, and AI visibility (AIO/AEO/GEO)
        # optimization. Left to plain fuzzy matching, this word is a subset
        # of "Search Engine Optimization (SEO)" and scores a perfect match
        # there every time, silently guessing SEO even when the customer
        # meant something else. Only fires when that's the ENTIRE meaningful
        # content of the message (e.g. not "seo optimization", which is
        # unambiguous).
        content = _content_tokens(clean)
        if content and content <= _AMBIGUOUS_SERVICE_WORDS:
            return {
                "answer": (
                    "We offer a few different optimization services - which one did you mean? "
                    "Search Engine Optimization (SEO), Google Business Profile Optimization, "
                    "or AI Visibility Optimization (AIO, AEO and GEO)?"
                ),
                "matched_faq_id": None,
                "match_score": 100,
                "intent": "service_clarify",
                "needs_escalation": False,
                "pending_pricing_topic": "",
            }

        service_hit = self._match_service(clean)
        if service_hit:
            return {
                "answer": f"{service_hit['name']}: {service_hit['description']}",
                "matched_faq_id": None,
                "match_score": 95,
                "intent": "service_info",
                "needs_escalation": False,
                "pending_pricing_topic": "",

                # Send the selected service back to app.py
                "selected_service": service_hit["name"],
            }

        # Fuzzy FAQ matching
        faq, score = self._best_faq_match(clean, last_bot_message=last_bot_message)
        if faq:
            if score >= Config.FUZZY_MATCH_THRESHOLD:
                return {
                    "answer": faq["answer"],
                    "matched_faq_id": faq["id"],
                    "match_score": score,
                    "intent": "faq_match",
                    "needs_escalation": False,
                    "pending_pricing_topic": "",
                }
            if score >= Config.FUZZY_FALLBACK_THRESHOLD:
                # Weak match - answer but flag for review
                return {
                    "answer": faq["answer"],
                    "matched_faq_id": faq["id"],
                    "match_score": score,
                    "intent": "faq_weak_match",
                    "needs_escalation": True,
                    "pending_pricing_topic": "",
                }

        # Fall back to searching any admin-uploaded reference documents (PDFs)
        doc_hit = self._search_documents(clean)
        if doc_hit:
            return {
                "answer": doc_hit["chunk_text"] + f"\n\n(source: {doc_hit['filename']})",
                "matched_faq_id": None,
                "match_score": doc_hit["score"],
                "intent": "document_match",
                "needs_escalation": doc_hit["score"] < Config.FUZZY_MATCH_THRESHOLD,
                "pending_pricing_topic": "",
            }

        return self._fallback()

    # ---------- Internal helpers ----------

    def _matches_any(self, text, patterns):
        if any(p in text for p in patterns):
            return True
        # fuzz.partial_ratio scores based on the shorter string's length, so
        # very short input text (e.g. "hi") can score ~100 against a long,
        # unrelated pattern purely because those couple of characters happen
        # to appear somewhere inside it (e.g. "hi" inside "rank HIgher on
        # google"). Skip the fuzzy branch entirely for short input - it has
        # no real typo-tolerance value there anyway, only false positives.
        if len(text) < 8:
            return False
        return any(fuzz.partial_ratio(text, p) >= 85 for p in patterns if len(p) > 4)

    def _is_greeting(self, text):
        """
        Word-boundary match against greeting patterns, restricted to short
        messages. Deliberately avoids fuzzy partial-ratio scoring here -
        short patterns like "hi" produce false positives against unrelated
        longer sentences (e.g. "where are you located").
        """
        if len(text.split()) > 4:
            return False
        words = re.findall(r"[a-z]+", text)
        for p in _GREETING_PATTERNS:
            if " " in p:
                if p in text:
                    return True
            elif p in words:
                return True
        return False

    def _flow_continuation_match(self, text, last_bot_message):
        """
        The scripted "flow_XXX" FAQ entries (flow_009_name, flow_010_business_name,
        etc., in knowledge/faqs/general.json) mimic a guided intake purely
        through keyword-matched FAQs - e.g. flow_010's keywords require a
        lead-in phrase like "my business name is" to score above the match
        threshold. A bare, on-topic reply typed straight after the bot asked
        that exact question (e.g. just "ABC" for "What is your business
        name?") has no keyword overlap and would otherwise fall through to
        the generic "I don't understand" fallback. This checks whether
        `last_bot_message` was one of these flow prompts and, if so, treats
        the reply as its answer without requiring the lead-in phrase.
        """
        if not last_bot_message or not text.strip():
            return None
        for faq in self.faqs:
            if not str(faq.get("id", "")).startswith("flow_"):
                continue
            ratio = fuzz.token_set_ratio(last_bot_message.lower(), faq["question"].lower())
            if ratio >= 70:
                return faq
        return None

    def _best_faq_match(self, text, last_bot_message=None):
        """
        Score every FAQ by comparing the user's text against its full question
        (full weight) and its keywords (down-weighted, and further down-weighted
        the shorter/more generic the keyword is, so a broad keyword like
        "digital marketing" can't outscore a specific full-question match).
        """
        text_tokens = _content_tokens(text)

        # best_ratio breaks ties between equally-scored FAQs by preferring
        # the one whose full question text is most literally similar to the
        # query (fuzz.ratio - character-level, order-sensitive). Needed
        # because token_set_ratio alone scores a short/subset query at 100
        # against MULTIPLE differently-worded questions that merely contain
        # the same words (e.g. "what is google business profile" ties at
        # 100 against both "What is Google Business Profile (GBP)?" and
        # "What is Google Business Profile optimization?" - the extra word
        # "optimization" doesn't lower token_set_ratio, since token_set_ratio
        # is insensitive to unmatched extra tokens in the target when the
        # query is a subset). Without this, whichever FAQ happens to appear
        # first in the file silently wins ties.
        best_faq, best_score, best_ratio = None, 0, None
        for faq in self.faqs:
            question_score = fuzz.token_set_ratio(text, faq["question"].lower())
            if not _content_overlap(text_tokens, _content_tokens(faq["question"])):
                # No real shared topic words - only generic filler overlaps,
                # so don't let this count as a question match.
                question_score = 0

            best_kw_score = 0
            for kw in faq.get("keywords", []):
                kw = kw.lower()
                if not _content_overlap(text_tokens, _content_tokens(kw)):
                    # Same guard for keywords: skip anything with zero
                    # meaningful word overlap instead of scoring it on
                    # shared filler words alone.
                    continue
                raw = fuzz.token_set_ratio(text, kw)
                # Penalize short/generic keywords relative to the query length
                specificity_penalty = max(0, 20 - (len(kw.split()) * 6))
                weighted = raw - specificity_penalty
                best_kw_score = max(best_kw_score, weighted)
            score = max(question_score, best_kw_score - 5)

            ratio = fuzz.ratio(text, faq["question"].lower())
            is_better = score > best_score or (
                score == best_score and best_ratio is not None and ratio > best_ratio
            )
            if is_better:
                best_score = score
                best_faq = faq
                best_ratio = ratio

        # Nothing scored well on its own - before giving up, check whether
        # the bot's last message was one of the scripted flow_XXX prompts
        # (e.g. "What is your business name?"). If so, treat this reply as
        # its answer even without a lead-in phrase like "my business name
        # is", rather than telling the visitor we don't understand a plain
        # "ABC" typed in direct response to that exact question.
        if best_score < Config.FUZZY_FALLBACK_THRESHOLD:
            flow_faq = self._flow_continuation_match(text, last_bot_message)
            if flow_faq:
                return flow_faq, 92

        return best_faq, best_score

    def _match_pricing_category(self, text):
        """
        First checks direct, unambiguous aliases (e.g. "aeo", "gbp") on word
        boundaries - these are exact abbreviation hits and should resolve
        with full confidence rather than going through fuzzy scoring, where
        a long multi-word category name like "AI-Powered SEO, AIO, AEO and
        GEO" dilutes the score even when the abbreviation is an obvious,
        unambiguous match. Falls back to fuzzy matching against category
        names only if no alias hits.
        """
        categories = self.website_data["pricing"].get("categories", {})

        for alias in sorted(_PRICING_ALIAS_TO_CATEGORY, key=len, reverse=True):
            cat_name = _PRICING_ALIAS_TO_CATEGORY[alias]
            if cat_name not in categories:
                continue
            if re.search(rf"\b{re.escape(alias)}\b", text):
                return cat_name, 100

        best_cat, best_score = None, 0
        for cat_name in categories:
            score = fuzz.token_set_ratio(text, cat_name.lower())
            if score > best_score:
                best_score, best_cat = score, cat_name
        return best_cat, best_score

    def _category_price_answer(self, cat_name):
        pricing = self.website_data["pricing"]
        categories = pricing["categories"]
        disclaimer = pricing["disclaimer_rule"]
        lines = [f"- {p['package']}: {p['price']}" for p in categories[cat_name]]
        return f"{cat_name} pricing:\n" + "\n".join(lines) + "\n\n" + disclaimer

    def _pricing_answer(self, text):
        """
        If the user named a specific category (e.g. "SEO", "GBP"), answer
        with only that category's packages. Otherwise, rather than dumping
        every category's price, ask which service they want pricing for -
        that's the professional, focused response.

        Returns (answer_text, resolved) where resolved is True only when a
        specific category was matched.
        """
        best_cat, best_score = self._match_pricing_category(text)

        if best_cat and best_score >= 65:
            return self._category_price_answer(best_cat), True

        categories = self.website_data["pricing"].get("categories", {})
        names = ", ".join(categories.keys())
        return (
            f"Happy to share pricing - which service would you like pricing for? "
            f"{names}."
        ), False

    def _explain_service_and_confirm(self, service_name):
        """
        Explains the named service using its existing description from
        website_data.json (no invented claims) and asks whether the visitor
        wants to proceed, so their next "yes" can skip straight into the
        guided intake instead of starting over.
        """
        service = self._service_lookup.get(service_name.lower())
        description = service["description"] if service else ""
        if description:
            body = f"Here's how {service_name} works with us:\n\n{description}"
        else:
            body = f"Happy to walk you through {service_name}."
        answer = f"{body}\n\nIf you are interested move forward YES or NO"
        return {
            "answer": answer,
            "matched_faq_id": None,
            "match_score": 100,
            "intent": "service_explain_confirm",
            "needs_escalation": False,
            "pending_pricing_topic": "",
            "selected_service": service_name,
        }

    def _start_lead_flow_with_service(self, service_name, known_contact=None):
        """
        Starts the guided purchase flow with the service question already
        answered from what we remember about this conversation
        (interested_service), so the visitor lands on the *next* question
        instead of being asked "What service are you interested in?" again
        from the top. Also pre-fills and skips any contact fields
        (full name / email / phone) we already captured earlier in this
        conversation - see `known_contact` on respond().
        """
        answers = {"service": service_name}
        answers.update({k: v for k, v in (known_contact or {}).items() if v})
        start_index = self._next_unanswered_step(self._flow_questions, answers, 0)
        if start_index >= len(self._flow_questions):
            start_index = len(self._flow_questions) - 1

        next_q = self._flow_questions[start_index]
        intro = (
            f"Great choice! Since you're interested in {service_name}, let's grab a "
            f"few quick details so we can put together your proposal."
        )
        return {
            "answer": f"{intro}\n\n{self._flow_question_text(start_index)}",
            "matched_faq_id": None,
            "match_score": 100,
            "intent": "lead_flow_start",
            "needs_escalation": False,
            "pending_pricing_topic": "",
            "options": next_q["options"],
            "lead_flow_step": start_index,
            "lead_flow_answers": answers,
            "lead_flow_complete": False,
            "lead_flow_data": None,
            "selected_service": service_name,
        }

    # ---------- Unified service flow (all services) ----------
    #
    # Every service button/trigger now runs through this single engine
    # instead of a separate hand-written flow per service:
    #
    #   description + benefits -> "want to learn more?"
    #     NO  -> back to the service menu
    #     YES -> FAQ menu (buttons, one per unasked question)
    #              -> pick a question -> show its answer
    #                   -> "another question?"
    #                        YES -> remaining FAQs (or purchase prompt if none left)
    #                        NO  -> "want to purchase?"
    #                                 NO  -> back to the service menu
    #                                 YES -> collect name/email/phone/company/
    #                                        budget/timeline/requirements one
    #                                        at a time -> confirm -> thank you
    #
    # Driven entirely by knowledge/service_faqs.json (self.service_faqs), so
    # adding a new service or FAQ set is a data change, not a code change.
    # State lives in lead_flow_answers, same mechanism as every other guided
    # flow (see respond()'s flow_marker dispatch): "_flow": "service_faq".

    _SERVICE_FLOW_LEAD_FIELDS = (
        ("full_name", "What's your full name?"),
        ("email", "What's the best email address to reach you?"),
        ("phone", "What's your phone number?"),
        ("business_name", "What's your company name?"),
        ("budget_range", "What's your budget for this project?"),
        ("start_timing", "What's your timeline to get started?"),
        ("requirements", "Any additional requirements or details we should know?"),
    )

    def _match_service_flow_trigger(self, clean_text):
        """Returns the service_faqs.json key whose trigger_patterns best
        match `clean_text`, or None.

        Exact (substring) matches always win over fuzzy ones, and among
        exact matches the longest/most specific pattern wins - otherwise a
        short, generic pattern in one service (e.g. "seo") can coincidentally
        fuzzy-outscore an exact, more specific match in another (e.g. "aio"
        in "ai_powered_seo" for the text "aio/aeo/geo services", where "seo
        services" fuzzy-matches at 92% purely by character overlap).
        """
        best_exact_key, best_exact_len = None, -1
        for key in self.service_faqs:
            for pattern in self.service_faqs[key].get("trigger_patterns", []):
                if pattern in clean_text and len(pattern) > best_exact_len:
                    best_exact_key, best_exact_len = key, len(pattern)
        if best_exact_key:
            return best_exact_key

        for key in self.service_faqs:
            patterns = self.service_faqs[key].get("trigger_patterns", [])
            if self._matches_any(clean_text, patterns):
                return key
        return None

    def _service_menu_reply(self, lead_in=None):
        """Shared 'NO' / purchase-declined destination for every stage of
        the flow - the flowchart's 'Return to Service Menu' box."""
        options = [{"label": svc["label"]} for svc in self.service_faqs.values()]
        text = lead_in or "No problem! Which service would you like to explore?"
        return {
            "answer": text,
            "matched_faq_id": None,
            "match_score": 100,
            "intent": "service_menu",
            "needs_escalation": False,
            "pending_pricing_topic": "",
            "options": options,
            "lead_flow_step": None,
            "lead_flow_answers": None,
            "lead_flow_complete": False,
            "lead_flow_data": None,
        }

    def _start_service_faq_flow(self, service_key, known_contact=None):
        svc = self.service_faqs[service_key]
        benefits = "\n".join(f"\u2022 {b}" for b in svc.get("benefits", []))
        intro = f"{svc['description']}"
        if benefits:
            intro += f"\n\n{benefits}"
        intro += "\n\nWould you like to learn more about this service?"
        answers = {"_flow": "service_faq", "_service_key": service_key, "_stage": "learn_more", "_asked": []}
        if known_contact:
            answers.update({k: v for k, v in known_contact.items() if v})
        return {
            "answer": intro,
            "matched_faq_id": None,
            "match_score": 100,
            "intent": "service_flow_start",
            "needs_escalation": False,
            "pending_pricing_topic": "",
            "options": [{"label": "Yes", "icon": "check-square"}, {"label": "No", "icon": "x-square"}],
            "lead_flow_step": 0,
            "lead_flow_answers": answers,
            "lead_flow_complete": False,
            "lead_flow_data": None,
            "selected_service": svc["label"],
        }

    def _service_faq_menu_reply(self, service_key, answers, lead_in=None):
        svc = self.service_faqs[service_key]
        asked = answers.get("_asked", [])
        remaining = [i for i in range(len(svc["faqs"])) if i not in asked]
        answers["_stage"] = "faq_menu"
        text = lead_in or f"Here are some common questions about {svc['label']} - tap one to learn more:"
        return {
            "answer": text,
            "matched_faq_id": None,
            "match_score": 100,
            "intent": "service_faq_menu",
            "needs_escalation": False,
            "pending_pricing_topic": "",
            "options": [{"label": svc["faqs"][i]["q"]} for i in remaining],
            "lead_flow_step": 0,
            "lead_flow_answers": answers,
            "lead_flow_complete": False,
            "lead_flow_data": None,
        }

    def _match_service_faq_question(self, faqs, text, asked):
        candidates = [i for i in range(len(faqs)) if i not in asked]
        if not candidates:
            return None
        labels = [faqs[i]["q"] for i in candidates]
        best = process.extractOne(text, labels, scorer=fuzz.token_set_ratio)
        if best and best[1] >= 60:
            return candidates[labels.index(best[0])]
        return None

    def _service_purchase_prompt(self, answers, lead_in=None):
        answers["_stage"] = "purchase_intent"
        text = lead_in or "Would you like to purchase this service?"
        return {
            "answer": text,
            "matched_faq_id": None,
            "match_score": 100,
            "intent": "service_purchase_prompt",
            "needs_escalation": False,
            "pending_pricing_topic": "",
            "options": [{"label": "Yes", "icon": "check-square"}, {"label": "No", "icon": "x-square"}],
            "lead_flow_step": 0,
            "lead_flow_answers": answers,
            "lead_flow_complete": False,
            "lead_flow_data": None,
        }

    def _service_retry(self, answers, prompt_text, options=None):
        return {
            "answer": f"Sorry, I didn't catch that. {prompt_text}",
            "matched_faq_id": None,
            "match_score": 100,
            "intent": "service_flow_retry",
            "needs_escalation": False,
            "pending_pricing_topic": "",
            "options": options,
            "lead_flow_step": 0,
            "lead_flow_answers": answers,
            "lead_flow_complete": False,
            "lead_flow_data": None,
        }

    def _advance_service_faq_flow(self, raw_text, step, answers, known_contact=None):
        answers = dict(answers or {})
        text = (raw_text or "").strip()
        lower = text.lower()
        service_key = answers.get("_service_key")
        svc = self.service_faqs.get(service_key)
        stage = answers.get("_stage")

        if not svc:
            return self._service_menu_reply()

        if lower in self._FLOW_CANCEL_WORDS:
            return {
                "answer": (
                    "No problem, I've cancelled that. Let me know whenever you'd "
                    "like to pick it back up, or ask me anything else in the meantime."
                ),
                "matched_faq_id": None,
                "match_score": 100,
                "intent": "lead_flow_cancelled",
                "needs_escalation": False,
                "pending_pricing_topic": "",
                "options": None,
                "lead_flow_step": None,
                "lead_flow_answers": None,
                "lead_flow_complete": False,
                "lead_flow_data": None,
            }

        yes_options = [{"label": "Yes", "icon": "check-square"}, {"label": "No", "icon": "x-square"}]

        # A visitor can ask about a completely different service while
        # mid-flow (e.g. typing "i need graphics designing services" while
        # being asked an SEO FAQ) - without this, they'd just get stuck in
        # a "sorry, I didn't catch that" loop tied to the wrong service.
        # Checked for every informational stage except faq_menu (there, a
        # genuine FAQ-question match should win first - see below) and the
        # lead-collection/confirm stages (there, free text is literal data
        # like a name or budget, not a request to switch topics).
        if stage in ("learn_more", "faq_answered", "purchase_intent"):
            switch_key = self._match_service_flow_trigger(lower)
            if switch_key and switch_key != service_key:
                pre_filled = {k: v for k, v in answers.items() if not k.startswith("_")}
                return self._start_service_faq_flow(switch_key, pre_filled or known_contact)

        # ---- "Would you like to learn more about this service?" ----
        if stage == "learn_more":
            if lower in self._YES_WORDS or self._match_option(lower, yes_options) == "Yes":
                if svc["faqs"]:
                    return self._service_faq_menu_reply(service_key, answers)
                return self._service_purchase_prompt(
                    answers,
                    lead_in=(
                        "We don't have any published FAQs for this service yet, but I'd "
                        "be happy to help directly. Would you like to purchase this service?"
                    ),
                )
            if lower in self._NO_WORDS or self._match_option(lower, yes_options) == "No":
                return self._service_menu_reply()
            return self._service_retry(answers, "Would you like to learn more about this service?", yes_options)

        # ---- FAQ menu: visitor picked (or typed) a question ----
        if stage == "faq_menu":
            idx = self._match_service_faq_question(svc["faqs"], text, answers.get("_asked", []))
            if idx is None:
                switch_key = self._match_service_flow_trigger(lower)
                if switch_key and switch_key != service_key:
                    pre_filled = {k: v for k, v in answers.items() if not k.startswith("_")}
                    return self._start_service_faq_flow(switch_key, pre_filled or known_contact)
                return self._service_faq_menu_reply(
                    service_key, answers,
                    lead_in="Sorry, I didn't catch that - please pick one of the questions below.",
                )
            answers.setdefault("_asked", []).append(idx)
            faq = svc["faqs"][idx]
            answers["_stage"] = "faq_answered"
            return {
                "answer": f"{faq['a']}\n\nDo you have another question?",
                "matched_faq_id": None,
                "match_score": 100,
                "intent": "service_faq_answer",
                "needs_escalation": False,
                "pending_pricing_topic": "",
                "options": yes_options,
                "lead_flow_step": 0,
                "lead_flow_answers": answers,
                "lead_flow_complete": False,
                "lead_flow_data": None,
            }

        # ---- "Do you have another question?" ----
        if stage == "faq_answered":
            if lower in self._YES_WORDS or self._match_option(lower, yes_options) == "Yes":
                remaining = [i for i in range(len(svc["faqs"])) if i not in answers.get("_asked", [])]
                if not remaining:
                    return self._service_purchase_prompt(
                        answers,
                        lead_in="That's all the common questions we've got for this service! Would you like to purchase this service?",
                    )
                return self._service_faq_menu_reply(service_key, answers)
            if lower in self._NO_WORDS or self._match_option(lower, yes_options) == "No":
                return self._service_purchase_prompt(answers)
            return self._service_retry(answers, "Do you have another question?", yes_options)

        # ---- "Would you like to purchase this service?" ----
        if stage == "purchase_intent":
            if lower in self._YES_WORDS or self._match_option(lower, yes_options) == "Yes":
                pre_filled = {k: v for k, v in answers.items() if not k.startswith("_")}
                for field, prompt in self._SERVICE_FLOW_LEAD_FIELDS:
                    if not pre_filled.get(field):
                        answers["_stage"] = f"collect_{field}"
                        return {
                            "answer": f"Great! Let's grab a few details so a specialist can follow up.\n\n{prompt}",
                            "matched_faq_id": None,
                            "match_score": 100,
                            "intent": "service_lead_collect_start",
                            "needs_escalation": False,
                            "pending_pricing_topic": "",
                            "options": None,
                            "lead_flow_step": 0,
                            "lead_flow_answers": answers,
                            "lead_flow_complete": False,
                            "lead_flow_data": None,
                        }
                return self._service_confirm_reply(answers)
            if lower in self._NO_WORDS or self._match_option(lower, yes_options) == "No":
                return self._service_menu_reply()
            return self._service_retry(answers, "Would you like to purchase this service?", yes_options)

        # ---- Sequential lead-info collection ----
        if isinstance(stage, str) and stage.startswith("collect_"):
            field = stage[len("collect_"):]

            if field == "email":
                result = validate_email(text)
                if not result["valid"]:
                    return self._service_retry(answers, result["message"])
                answers["email"] = result["email"]
            elif field == "phone":
                result = validate_phone(text)
                if not result["valid"]:
                    return self._service_retry(answers, result["message"])
                answers["phone"] = result["phone"]
            else:
                answers[field] = text

            for next_field, prompt in self._SERVICE_FLOW_LEAD_FIELDS:
                if not answers.get(next_field):
                    answers["_stage"] = f"collect_{next_field}"
                    return {
                        "answer": prompt,
                        "matched_faq_id": None,
                        "match_score": 100,
                        "intent": "service_lead_collect",
                        "needs_escalation": False,
                        "pending_pricing_topic": "",
                        "options": None,
                        "lead_flow_step": 0,
                        "lead_flow_answers": answers,
                        "lead_flow_complete": False,
                        "lead_flow_data": None,
                    }
            return self._service_confirm_reply(answers)

        # ---- Confirm collected info before finalizing ----
        if stage == "confirm":
            if lower in self._YES_WORDS or self._match_option(lower, yes_options) == "Yes":
                c = self.website_data["contact"]
                closing = (
                    "Thank you! A specialist will contact you shortly "
                    f"(or call us anytime at {c['phone']} if you'd like to speed things along)."
                )
                lead_data = {k: v for k, v in answers.items() if not k.startswith("_")}
                lead_data["service"] = svc["label"]
                return {
                    "answer": closing,
                    "matched_faq_id": None,
                    "match_score": 100,
                    "intent": "lead_flow_complete",
                    "needs_escalation": False,
                    "pending_pricing_topic": "",
                    "options": None,
                    "lead_flow_step": None,
                    "lead_flow_answers": None,
                    "lead_flow_complete": True,
                    "lead_flow_data": lead_data,
                    "selected_service": svc["label"],
                }
            if lower in self._NO_WORDS or self._match_option(lower, yes_options) == "No":
                answers["_stage"] = "collect_full_name"
                for field, _ in self._SERVICE_FLOW_LEAD_FIELDS:
                    answers.pop(field, None)
                return {
                    "answer": "No problem, let's redo it. What's your full name?",
                    "matched_faq_id": None,
                    "match_score": 100,
                    "intent": "service_lead_collect_restart",
                    "needs_escalation": False,
                    "pending_pricing_topic": "",
                    "options": None,
                    "lead_flow_step": 0,
                    "lead_flow_answers": answers,
                    "lead_flow_complete": False,
                    "lead_flow_data": None,
                }
            return self._service_retry(answers, "Does everything above look correct?", yes_options)

        # Safety net - should be unreachable given the stages above.
        return self._service_menu_reply()

    def _service_confirm_reply(self, answers):
        summary_lines = []
        for field, label in (
            ("full_name", "Name"), ("email", "Email"), ("phone", "Phone"),
            ("business_name", "Company"), ("budget_range", "Budget"),
            ("start_timing", "Timeline"), ("requirements", "Additional requirements"),
        ):
            value = answers.get(field)
            if value:
                summary_lines.append(f"\u2022 {label}: {value}")
        answers["_stage"] = "confirm"
        summary = "\n".join(summary_lines)
        return {
            "answer": f"Here's what I've got:\n\n{summary}\n\nDoes everything above look correct?",
            "matched_faq_id": None,
            "match_score": 100,
            "intent": "service_lead_confirm",
            "needs_escalation": False,
            "pending_pricing_topic": "",
            "options": [{"label": "Yes", "icon": "check-square"}, {"label": "No", "icon": "x-square"}],
            "lead_flow_step": 0,
            "lead_flow_answers": answers,
            "lead_flow_complete": False,
            "lead_flow_data": None,
        }

    def _purchase_answer(self, interested_service=None):
        """
        Gives a concrete, professional path to purchase instead of the
        generic "what service are you interested in?" loop. If we already
        know which service the customer discussed, we reference it by name
        and skip re-asking.
        """
        c = self.website_data["contact"]
        if interested_service:
            intro = f"Happy to get {interested_service} started for you. Here's exactly how it works:"
        else:
            intro = "Happy to get you started. Here's exactly how it works:"

        steps = (
            "1) We confirm the scope with you (what's included, timeline, any add-ons).\n"
            "2) We send a written proposal and invoice.\n"
            "3) A deposit secures your start date, and we begin on the agreed schedule."
        )
        ask = (
            f"Could you share your name, email, and which service you're interested in "
            f"(or call us at {c['phone']}) so we can send that proposal over today?"
        )
        return f"{intro}\n\n{steps}\n\n{ask}"

    def _match_service(self, text):
        # Direct alias check first, on word boundaries. Fuzzy scoring alone
        # ties "gbp optimization" between the SEO and GBP service names
        # (both contain "optimization"), and picks whichever came first in
        # the list - so an abbreviation like "gbp" needs to resolve
        # deterministically instead of relying on the tie-break.
        for alias in sorted(_SERVICE_ALIAS_TO_NAME, key=len, reverse=True):
            if re.search(rf"\b{re.escape(alias)}\b", text):
                name = _SERVICE_ALIAS_TO_NAME[alias]
                if name in self._service_lookup:
                    return self._service_lookup[name]

        names = list(self._service_lookup.keys())
        cleaned = _strip_service_filler(text)
        best = process.extractOne(cleaned, names, scorer=fuzz.token_set_ratio)
        if best and best[1] >= 80:
            return self._service_lookup[best[0]]
        return None

    def _search_documents(self, text):
        """
        Fuzzy-searches text chunks extracted from admin-uploaded PDFs. Queried
        live (not cached) so newly uploaded documents are searchable right
        away without restarting the app.
        """
        # Imported here (not top-level) to avoid a hard dependency between
        # chat_engine and the database layer at import time for callers that
        # only need FAQ/service matching.
        from database import db

        chunks = db.get_all_knowledge_chunks()
        if not chunks:
            return None

        best_chunk, best_score = None, 0
        for chunk in chunks:
            score = fuzz.token_set_ratio(text, chunk["chunk_text"].lower())
            if score > best_score:
                best_score = score
                best_chunk = chunk

        if best_chunk and best_score >= Config.FUZZY_FALLBACK_THRESHOLD:
            return {**best_chunk, "score": best_score}
        return None

    def _fallback(self):
        return {
            "answer": self.website_data["greetings"]["fallback_message"],
            "matched_faq_id": None,
            "match_score": 0,
            "intent": "unanswered",
            "needs_escalation": True,
            "pending_pricing_topic": "",
        }