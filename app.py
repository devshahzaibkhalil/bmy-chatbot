"""
app.py
BMY Marketer AI Assistant - Flask backend.

Runs entirely locally. No OpenAI/Gemini/paid AI API is used anywhere -
matching is done with fuzzywuzzy against the local knowledge base in
knowledge/website_data.json and knowledge/faqs/*.json.
"""

from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import json
import os

from config import Config, ensure_directories
from chat_engine import ChatEngine
from database import db
import leads
import appointments
import notifications
import scheduler
import file_manager
import voice_tools
from admin_routes import admin_bp
from rate_limit import rate_limited
from validation.email_validator import validate_email
from validation.phone_validator import validate_phone

ensure_directories()
db.init_db()
scheduler.start()

app = Flask(__name__)
app.config.from_object(Config)  # includes SESSION_COOKIE_SECURE, PERMANENT_SESSION_LIFETIME
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)
# The public /api/* endpoints (chat widget) are identified by conversation_id,
# not a cookie - so they're safe to open to any embedding domain without
# supports_credentials. Admin auth (/admin/api/*) is a separate blueprint,
# uses a real session cookie, and is never covered by this CORS rule.
CORS(app, resources={r"/api/*": {"origins": Config.CORS_ORIGINS}})
app.register_blueprint(admin_bp)

engine = ChatEngine()


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


DEFAULT_WIDGET_THEME = "professional_navy"
ALLOWED_WIDGET_THEMES = ("professional_navy", "charcoal_gold", "corporate_blue", "slate_teal", "deep_green")


@app.route("/api/theme", methods=["GET"])
def get_widget_theme():
    """Public - the chat widget calls this on load to know which color
    theme to render, so a change made in the admin dashboard applies
    everywhere the widget is embedded without editing any code."""
    theme = db.get_setting("widget_theme", DEFAULT_WIDGET_THEME)
    if theme not in ALLOWED_WIDGET_THEMES:
        theme = DEFAULT_WIDGET_THEME
    return jsonify({"theme": theme})


# ---------------------------------------------------------------------------
# Chat API
# ---------------------------------------------------------------------------

@app.route("/api/chat/start", methods=["POST"])
@rate_limited
def chat_start():
    """
    Start (or resume) a session.
    Body: { session_id, email?, phone?, browser_info?, device_info? }
    If email/phone matches an existing customer, their conversation history
    is returned so the widget can greet them as a returning visitor.
    """
    data = request.get_json(force=True) or {}
    session_id = data.get("session_id") or db.new_id()
    email = data.get("email")
    phone = data.get("phone")

    if email:
        email_result = validate_email(email)
        if not email_result["valid"]:
            return jsonify({
                "error": email_result["message"],
                "suggestion": email_result["suggestion"],
            }), 400
        email = email_result["email"]

    if phone:
        phone_result = validate_phone(phone)
        if not phone_result["valid"]:
            return jsonify({"error": phone_result["message"]}), 400
        phone = phone_result["phone"]

    customer = db.find_customer_by_contact(email=email, phone=phone)
    returning = customer is not None

    if customer:
        db.update_customer(
            customer["id"],
            browser_info=data.get("browser_info"),
            device_info=data.get("device_info"),
        )
        customer_id = customer["id"]
        history = db.get_customer_conversations(customer_id, limit=5)
    else:
        customer_id = db.create_customer(
            email=email,
            phone=phone,
            ip_address=request.headers.get("X-Forwarded-For", request.remote_addr),
            browser_info=data.get("browser_info"),
            device_info=data.get("device_info"),
        )
        history = []

    # Resume an existing open conversation for this browser session instead
    # of always starting a new one - otherwise every page reload/reopen wipes
    # the visible widget back down to just the greeting, even though the
    # customer already had a longer conversation stored in the database.
    existing_conv = db.get_open_conversation_by_session(session_id)
    prior_messages = []
    if existing_conv:
        conversation_id = existing_conv["id"]
        prior_messages = db.get_conversation_messages(conversation_id)
        greeting = None
    else:
        conversation_id = db.create_conversation(session_id=session_id, customer_id=customer_id)
        notifications.notify("new_conversation", conversation_id=conversation_id)
        if returning:
            greeting = "Welcome back! Great to see you again. How can I help you today?"
        else:
            greeting = engine.get_welcome_message()
        db.add_message(conversation_id, sender="bot", message=greeting)

    return jsonify({
        "session_id": session_id,
        "conversation_id": conversation_id,
        "customer_id": customer_id,
        "returning_customer": returning,
        "greeting": greeting,
        "messages": [
            {"sender": m["sender"], "message": m["message"]} for m in prior_messages
        ],
        "previous_conversations": len(history),
    })


@app.route("/api/chat/message", methods=["POST"])
@rate_limited
def chat_message():
    """
    Body: { conversation_id, message }
    Returns the bot's reply and stores both sides of the exchange.
    """
    data = request.get_json(force=True) or {}
    conversation_id = data.get("conversation_id")
    message = (data.get("message") or "").strip()

    conv = conversation_id and db.get_conversation(conversation_id)
    if not conversation_id or not conv:
        return jsonify({"error": "Invalid or missing conversation_id. Call /api/chat/start first."}), 400
    if conv.get("status") and conv.get("status") != "open":
        # The conversation was ended (via /api/chat/end or a closed tab) -
        # it stays ended. No further messages are processed, matching the
        # front-end's "conversation ended" locked state.
        return jsonify({
            "error": "This conversation has ended. Start a new chat to keep talking with us.",
            "code": "conversation_ended",
        }), 410
    if not message:
        return jsonify({"error": "message is required."}), 400

    db.add_message(conversation_id, sender="customer", message=message)

    conv = db.get_conversation(conversation_id)
    lead_flow_step = (conv or {}).get("lead_flow_step")
    lead_flow_answers = None
    if lead_flow_step is not None:
        raw = (conv or {}).get("lead_flow_answers")
        lead_flow_answers = json.loads(raw) if raw else {}

    prior_messages = db.get_conversation_messages(conversation_id)
    last_bot_message = None
    for m in reversed(prior_messages):
        if m.get("sender") == "bot":
            last_bot_message = m.get("message")
            break

    # If we already captured this visitor's name/email/phone earlier in
    # the conversation (e.g. a previous guided flow completed and saved it
    # via db.update_customer below), don't make them re-type it for a
    # second service they explore afterward.
    known_contact = None
    if conv and conv.get("customer_id"):
        customer = db.get_customer(conv["customer_id"])
        if customer:
            known_contact = {
                "full_name": customer.get("full_name"),
                "email": customer.get("email"),
                "phone": customer.get("phone"),
            }

    result = engine.respond(
        message,
        interested_service=(conv or {}).get("interested_service"),
        pending_pricing_topic=(conv or {}).get("pending_pricing_topic"),
        lead_flow_step=lead_flow_step,
        lead_flow_answers=lead_flow_answers,
        last_bot_message=last_bot_message,
        known_contact=known_contact,
    )

    db.add_message(
        conversation_id,
        sender="bot",
        message=result["answer"],
        matched_faq_id=result["matched_faq_id"],
        match_score=result["match_score"],
    )
    db.update_conversation_intent(
        conversation_id, pending_pricing_topic=result.get("pending_pricing_topic", "")
    )
    db.update_conversation_lead_flow(
        conversation_id,
        step=result.get("lead_flow_step"),
        answers_json=(json.dumps(result["lead_flow_answers"]) if result.get("lead_flow_answers") else None),
    )

    # Auto-capture contact info mentioned inline
    contact = engine.extract_contact_info(message)
    if conv and conv.get("customer_id") and (contact["email"] or contact["phone"]):
        db.update_customer(conv["customer_id"], email=contact["email"], phone=contact["phone"])

    if result.get("needs_escalation"):
        notifications.notify("unanswered_question", conversation_id=conversation_id, message=message)

    selected_service = result.get("selected_service")

    if not selected_service:
        lead_answers = result.get("lead_flow_answers") or {}
        selected_service = lead_answers.get("service")

    if not selected_service:
        lead_data = result.get("lead_flow_data") or {}
        selected_service = lead_data.get("service")

    if selected_service:
        db.update_conversation_interested_service(
            conversation_id,
            selected_service,
        )

    # Guided purchase/qualification flow finished this turn - build the lead
    # from the full 15-question answer set instead of the usual one-line
    # buying-intent heuristic below (which would otherwise also fire on
    # answers like "yes" or "$5,000" partway through the flow).
    if result.get("lead_flow_complete") and conv:
        answers = result.get("lead_flow_data") or {}
        is_specialist_flow = answers.get("_flow") == "specialist"
        if answers.get("full_name") or answers.get("email") or answers.get("phone"):
            db.update_customer(
                conv["customer_id"],
                full_name=answers.get("full_name"),
                email=answers.get("email"),
                phone=answers.get("phone"),
                company_name=answers.get("business_name"),
                website_url=answers.get("website"),
            )
        # The specialist/consultation flow (chat_engine.py's
        # _specialist_flow_questions) is a different, shorter question set
        # than the main 15-question purchase flow - use whichever one
        # actually produced these answers so the summary includes every
        # field that was collected (e.g. "goals", which only exists on the
        # specialist flow).
        flow_questions = (
            engine._specialist_flow_questions if is_specialist_flow else engine._flow_questions
        )
        summary_lines = [f"{q['prompt']} {answers.get(q['key'], '')}".strip()
                          for q in flow_questions if answers.get(q["key"])]
        # The unified service-flow engine (chat_engine.py's
        # _advance_service_faq_flow) collects a free-text "requirements"
        # field that has no matching entry in either question list above -
        # add it directly so it still shows up in the lead's summary.
        if answers.get("requirements"):
            summary_lines.append(f"Additional requirements: {answers['requirements']}")
        lead_id = db.create_lead(
            customer_id=conv.get("customer_id"),
            conversation_id=conversation_id,
            name=answers.get("full_name"),
            email=answers.get("email"),
            phone=answers.get("phone"),
            company_name=answers.get("business_name"),
            interested_service=answers.get("service"),
            budget=answers.get("budget_range"),
            timeline=answers.get("start_timing"),
            conversation_summary=" | ".join(summary_lines),
        )
        db.update_conversation_intent(
            conversation_id, interested_service=answers.get("service"),
            budget=answers.get("budget_range"), timeline=answers.get("start_timing"),
        )
        db.log_event("new_lead", conversation_id=conversation_id, payload=lead_id)
        notifications.notify(
            "appointment_booked" if is_specialist_flow else "quote_request",
            conversation_id=conversation_id, message=message,
        )
    elif conv and lead_flow_step is None and result.get("lead_flow_step") is None:
        # Not in (or entering) the guided flow - keep the original one-line
        # buying-intent auto-capture for anything said outside that flow.
        interested_service = conv.get("interested_service")
        lead_id = leads.maybe_create_lead(
            conversation_id, conv.get("customer_id"), message, interested_service=interested_service
        )
        if lead_id:
            notifications.notify("quote_request", conversation_id=conversation_id, message=message)

    # Automatic appointment/consultation request capture
    if conv:
        appt_id = appointments.maybe_create_appointment(conversation_id, conv.get("customer_id"), message)
        if appt_id:
            notifications.notify("appointment_booked", conversation_id=conversation_id, message=message)

    return jsonify({
        "answer": result["answer"],
        "intent": result["intent"],
        "match_score": result["match_score"],
        "needs_escalation": result["needs_escalation"],
        "options": result.get("options"),
    })


@app.route("/api/chat/end", methods=["POST"])
def chat_end():
    data = request.get_json(force=True) or {}
    conversation_id = data.get("conversation_id")
    if not conversation_id:
        return jsonify({"error": "conversation_id is required."}), 400
    db.close_conversation(conversation_id, status="closed")
    return jsonify({"status": "closed"})


@app.route("/api/chat/history/<conversation_id>", methods=["GET"])
def chat_history(conversation_id):
    messages = db.get_conversation_messages(conversation_id)
    return jsonify({"conversation_id": conversation_id, "messages": messages})


@app.route("/api/chat/upload", methods=["POST"])
@rate_limited
def chat_upload():
    """
    Customer uploads a file (PDF/doc/image) during a chat.
    multipart/form-data: conversation_id, file
    """
    conversation_id = request.form.get("conversation_id")
    conv = db.get_conversation(conversation_id) if conversation_id else None
    if not conv:
        return jsonify({"error": "Invalid or missing conversation_id."}), 400
    if "file" not in request.files:
        return jsonify({"error": "No file provided."}), 400

    try:
        record = file_manager.save_upload(
            request.files["file"],
            purpose="customer_upload",
            customer_id=conv.get("customer_id"),
            conversation_id=conversation_id,
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    note = f"Customer uploaded a file: {record['filename']}"
    db.add_message(conversation_id, sender="bot", message=f"Got it - I've received \"{record['filename']}\". Our team will take a look.")
    notifications.notify("file_uploaded", conversation_id=conversation_id, message=note)

    return jsonify({
        "file_id": record["id"],
        "filename": record["filename"],
        "extraction_status": record["extraction_status"],
    })


@app.route("/api/chat/voice", methods=["POST"])
@rate_limited
def chat_voice():
    """
    Customer sends a voice message during a chat.
    multipart/form-data: conversation_id, audio (WAV/AIFF/FLAC)
    Transcribes the audio, then runs it through the normal chat pipeline.
    """
    conversation_id = request.form.get("conversation_id")
    conv = db.get_conversation(conversation_id) if conversation_id else None
    if not conv:
        return jsonify({"error": "Invalid or missing conversation_id."}), 400
    if "audio" not in request.files:
        return jsonify({"error": "No audio file provided."}), 400

    import tempfile
    audio_file = request.files["audio"]
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        audio_file.save(tmp.name)
        tmp_path = tmp.name

    result = voice_tools.transcribe_audio(tmp_path)
    try:
        os.remove(tmp_path)
    except OSError:
        pass

    if result["error"] or not result["text"]:
        return jsonify({"error": result["error"] or "Could not transcribe audio."}), 422

    # Reuse the same pipeline as a typed message
    transcript = result["text"]
    db.add_message(conversation_id, sender="customer", message=transcript)

    lead_flow_step = conv.get("lead_flow_step")
    lead_flow_answers = None
    if lead_flow_step is not None:
        raw = conv.get("lead_flow_answers")
        lead_flow_answers = json.loads(raw) if raw else {}

    known_contact = None
    if conv.get("customer_id"):
        customer = db.get_customer(conv["customer_id"])
        if customer:
            known_contact = {
                "full_name": customer.get("full_name"),
                "email": customer.get("email"),
                "phone": customer.get("phone"),
            }

    engine_result = engine.respond(
        transcript,
        interested_service=conv.get("interested_service"),
        pending_pricing_topic=conv.get("pending_pricing_topic"),
        lead_flow_step=lead_flow_step,
        lead_flow_answers=lead_flow_answers,
        known_contact=known_contact,
    )
    db.add_message(
        conversation_id, sender="bot", message=engine_result["answer"],
        matched_faq_id=engine_result["matched_faq_id"], match_score=engine_result["match_score"],
    )
    db.update_conversation_lead_flow(
        conversation_id,
        step=engine_result.get("lead_flow_step"),
        answers_json=(json.dumps(engine_result["lead_flow_answers"]) if engine_result.get("lead_flow_answers") else None),
    )

    return jsonify({
        "transcript": transcript,
        "answer": engine_result["answer"],
        "intent": engine_result["intent"],
        "options": engine_result.get("options"),
    })


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "BMY Marketer AI Assistant"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=Config.DEBUG)