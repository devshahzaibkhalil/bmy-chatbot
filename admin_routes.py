"""
admin_routes.py
Admin dashboard API - authentication, conversation search/filter/manage,
lead management, exports, and analytics. Registered on the main Flask app
in app.py as the /admin blueprint.
"""

from flask import Blueprint, request, jsonify, session, send_file, render_template
import io
import os

from database import db
from auth import attempt_login, login_required, role_required, hash_password
import exports
import backup
import file_manager
from rate_limit import rate_limited

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@admin_bp.route("/login", methods=["GET"])
def login_page():
    return render_template("admin_login.html")


@admin_bp.route("/dashboard", methods=["GET"])
def dashboard_page():
    return render_template("admin_dashboard.html")


@admin_bp.route("/api/login", methods=["POST"])
@rate_limited
def api_login():
    data = request.get_json(force=True) or {}
    user = attempt_login(data.get("username", ""), data.get("password", ""))
    if not user:
        return jsonify({"error": "Invalid username or password."}), 401
    session.permanent = True
    session["admin_username"] = user["username"]
    session["admin_role"] = user["role"]
    session["admin_user_id"] = user["id"]
    return jsonify({"username": user["username"], "role": user["role"]})


@admin_bp.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"status": "logged out"})


@admin_bp.route("/api/session", methods=["GET"])
def api_session():
    if not session.get("admin_username"):
        return jsonify({"authenticated": False}), 200
    return jsonify({
        "authenticated": True,
        "username": session["admin_username"],
        "role": session.get("admin_role"),
    })


# ---------------------------------------------------------------------------
# Admin user management (superadmin only)
# ---------------------------------------------------------------------------

@admin_bp.route("/api/admin-users", methods=["GET"])
@role_required("superadmin")
def api_list_admin_users():
    return jsonify({"admin_users": db.list_admin_users()})


@admin_bp.route("/api/admin-users", methods=["POST"])
@role_required("superadmin")
def api_create_admin_user():
    data = request.get_json(force=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    role = data.get("role", "agent")

    if role not in ("agent", "admin", "superadmin"):
        return jsonify({"error": "role must be one of: agent, admin, superadmin"}), 400
    if not username or len(password) < 8:
        return jsonify({"error": "username is required and password must be at least 8 characters."}), 400
    if db.get_admin_by_username(username):
        return jsonify({"error": "That username is already taken."}), 400

    user_id = db.create_admin_user(username, hash_password(password), role=role)
    return jsonify({"id": user_id, "username": username, "role": role})


@admin_bp.route("/api/admin-users/<user_id>", methods=["DELETE"])
@role_required("superadmin")
def api_delete_admin_user(user_id):
    if user_id == session.get("admin_user_id"):
        return jsonify({"error": "You can't delete your own account while logged in."}), 400
    db.delete_admin_user(user_id)
    return jsonify({"status": "deleted"})


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

@admin_bp.route("/api/conversations", methods=["GET"])
@login_required
def api_list_conversations():
    search = request.args.get("search")
    status = request.args.get("status")
    service = request.args.get("service")
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")
    limit = int(request.args.get("limit", 50))
    offset = int(request.args.get("offset", 0))

    rows = db.list_conversations(
        search=search, status=status, service=service,
        date_from=date_from, date_to=date_to, limit=limit, offset=offset,
    )
    return jsonify({"conversations": rows, "count": len(rows)})


@admin_bp.route("/api/conversations/<conversation_id>", methods=["GET"])
@login_required
def api_conversation_detail(conversation_id):
    conv = db.get_conversation_detail(conversation_id)
    if not conv:
        return jsonify({"error": "Conversation not found."}), 404
    return jsonify(conv)


@admin_bp.route("/api/conversations/<conversation_id>/status", methods=["POST"])
@login_required
def api_update_status(conversation_id):
    data = request.get_json(force=True) or {}
    status = data.get("status")
    if status not in ("open", "closed", "escalated", "resolved"):
        return jsonify({"error": "status must be one of: open, closed, escalated, resolved"}), 400
    db.update_conversation_status(conversation_id, status)
    return jsonify({"status": status})


@admin_bp.route("/api/conversations/<conversation_id>/notes", methods=["POST"])
@login_required
def api_add_note(conversation_id):
    data = request.get_json(force=True) or {}
    note = (data.get("note") or "").strip()
    if not note:
        return jsonify({"error": "note is required."}), 400
    note_id = db.add_internal_note(conversation_id, note, admin_username=session.get("admin_username"))
    return jsonify({"note_id": note_id})


@admin_bp.route("/api/conversations/<conversation_id>/reply", methods=["POST"])
@login_required
def api_manual_reply(conversation_id):
    """Lets an admin step into a conversation and send a message as the bot/agent."""
    data = request.get_json(force=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"error": "message is required."}), 400
    if not db.get_conversation(conversation_id):
        return jsonify({"error": "Conversation not found."}), 404
    db.add_message(conversation_id, sender="bot", message=message)
    return jsonify({"status": "sent"})


@admin_bp.route("/api/conversations/<conversation_id>", methods=["DELETE"])
@login_required
def api_delete_conversation(conversation_id):
    """Soft delete by default - recoverable via /restore. Pass ?permanent=true to purge
    (requires admin or superadmin - agents can soft-delete but not permanently purge)."""
    if request.args.get("permanent") == "true":
        if session.get("admin_role") not in ("admin", "superadmin"):
            return jsonify({"error": "Permanent deletion requires an admin or superadmin role."}), 403
        db.delete_conversation(conversation_id)
        return jsonify({"status": "permanently_deleted"})
    db.soft_delete_conversation(conversation_id)
    return jsonify({"status": "deleted"})


@admin_bp.route("/api/conversations/<conversation_id>/restore", methods=["POST"])
@login_required
def api_restore_conversation(conversation_id):
    db.restore_conversation(conversation_id)
    return jsonify({"status": "restored"})


@admin_bp.route("/api/conversations/deleted", methods=["GET"])
@login_required
def api_list_deleted_conversations():
    limit = int(request.args.get("limit", 50))
    offset = int(request.args.get("offset", 0))
    rows = db.list_deleted_conversations(limit=limit, offset=offset)
    return jsonify({"conversations": rows, "count": len(rows)})


# ---------------------------------------------------------------------------
# Leads
# ---------------------------------------------------------------------------

@admin_bp.route("/api/leads", methods=["GET"])
@login_required
def api_list_leads():
    status = request.args.get("status")
    search = request.args.get("search")
    limit = int(request.args.get("limit", 50))
    offset = int(request.args.get("offset", 0))
    rows = db.list_leads(status=status, search=search, limit=limit, offset=offset)
    return jsonify({"leads": rows, "count": len(rows)})


@admin_bp.route("/api/leads/<lead_id>/status", methods=["POST"])
@login_required
def api_update_lead_status(lead_id):
    data = request.get_json(force=True) or {}
    status = data.get("status")
    if status not in ("new", "contacted", "proposal_sent", "won", "lost"):
        return jsonify({"error": "invalid status"}), 400
    db.update_lead_status(lead_id, status)
    return jsonify({"status": status})


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

@admin_bp.route("/api/analytics/summary", methods=["GET"])
@login_required
def api_analytics_summary():
    return jsonify(db.get_analytics_summary())


@admin_bp.route("/api/analytics/daily", methods=["GET"])
@login_required
def api_analytics_daily():
    days = int(request.args.get("days", 7))
    return jsonify({"daily": db.get_daily_report(days=days)})


@admin_bp.route("/api/analytics/weekly", methods=["GET"])
@login_required
def api_analytics_weekly():
    weeks = int(request.args.get("weeks", 8))
    return jsonify({"weekly": db.get_weekly_report(weeks=weeks)})


@admin_bp.route("/api/analytics/monthly", methods=["GET"])
@login_required
def api_analytics_monthly():
    months = int(request.args.get("months", 12))
    return jsonify({"monthly": db.get_monthly_report(months=months)})


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

@admin_bp.route("/api/notifications", methods=["GET"])
@login_required
def api_list_notifications():
    unread_only = request.args.get("unread_only") == "true"
    rows = db.list_notifications(unread_only=unread_only)
    return jsonify({"notifications": rows, "unread_count": db.unread_notification_count()})


@admin_bp.route("/api/notifications/<notification_id>/read", methods=["POST"])
@login_required
def api_mark_notification_read(notification_id):
    db.mark_notification_read(notification_id)
    return jsonify({"status": "read"})


@admin_bp.route("/api/notifications/read-all", methods=["POST"])
@login_required
def api_mark_all_notifications_read():
    db.mark_all_notifications_read()
    return jsonify({"status": "all_read"})


# ---------------------------------------------------------------------------
# Appointments
# ---------------------------------------------------------------------------

@admin_bp.route("/api/appointments", methods=["GET"])
@login_required
def api_list_appointments():
    status = request.args.get("status")
    limit = int(request.args.get("limit", 50))
    offset = int(request.args.get("offset", 0))
    rows = db.list_appointments(status=status, limit=limit, offset=offset)
    return jsonify({"appointments": rows, "count": len(rows)})


@admin_bp.route("/api/appointments/<appointment_id>/status", methods=["POST"])
@login_required
def api_update_appointment_status(appointment_id):
    data = request.get_json(force=True) or {}
    status = data.get("status")
    if status not in ("requested", "confirmed", "completed", "cancelled"):
        return jsonify({"error": "invalid status"}), 400
    db.update_appointment_status(appointment_id, status)
    return jsonify({"status": status})


# ---------------------------------------------------------------------------
# Backups & recovery (superadmin only - restoring overwrites live data)
# ---------------------------------------------------------------------------

@admin_bp.route("/api/backups", methods=["GET"])
@login_required
def api_list_backups():
    return jsonify({"backups": backup.list_backups()})


@admin_bp.route("/api/backups/run", methods=["POST"])
@role_required("superadmin")
def api_run_backup():
    path = backup.backup_now()
    if not path:
        return jsonify({"error": "No live database found to back up yet."}), 400
    return jsonify({"status": "backed_up", "path": path})


@admin_bp.route("/api/backups/restore", methods=["POST"])
@role_required("superadmin")
def api_restore_backup():
    data = request.get_json(force=True) or {}
    filename = data.get("filename")
    if not filename:
        return jsonify({"error": "filename is required."}), 400
    try:
        backup.restore_from_backup(filename)
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    return jsonify({"status": "restored", "filename": filename})


# ---------------------------------------------------------------------------
# Knowledge base documents (admin-uploaded PDFs, searchable by the chatbot)
# ---------------------------------------------------------------------------

@admin_bp.route("/api/knowledge/documents", methods=["GET"])
@login_required
def api_list_knowledge_documents():
    rows = db.list_files(purpose="knowledge_base")
    return jsonify({"documents": rows, "count": len(rows)})


@admin_bp.route("/api/knowledge/documents", methods=["POST"])
@role_required("admin", "superadmin")
def api_upload_knowledge_document():
    if "file" not in request.files:
        return jsonify({"error": "No file provided."}), 400
    try:
        record = file_manager.save_upload(request.files["file"], purpose="knowledge_base")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(record)


@admin_bp.route("/api/knowledge/documents/<file_id>", methods=["DELETE"])
@role_required("admin", "superadmin")
def api_delete_knowledge_document(file_id):
    record = db.get_file(file_id)
    if not record:
        return jsonify({"error": "Document not found."}), 404
    if record["stored_path"] and os.path.exists(record["stored_path"]):
        try:
            os.remove(record["stored_path"])
        except OSError:
            pass
    db.delete_file_record(file_id)
    return jsonify({"status": "deleted"})


# ---------------------------------------------------------------------------
# Customer-uploaded files (from the chat widget)
# ---------------------------------------------------------------------------

@admin_bp.route("/api/files", methods=["GET"])
@login_required
def api_list_customer_files():
    conversation_id = request.args.get("conversation_id")
    rows = db.list_files(purpose="customer_upload", conversation_id=conversation_id)
    return jsonify({"files": rows, "count": len(rows)})


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

@admin_bp.route("/api/export/conversations", methods=["GET"])
@login_required
def api_export_conversations():
    fmt = request.args.get("format", "csv")
    search = request.args.get("search")
    status = request.args.get("status")
    rows = db.list_conversations(search=search, status=status, limit=10000)
    try:
        content, mimetype, filename = exports.export_conversations(rows, fmt)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return send_file(io.BytesIO(content), mimetype=mimetype, as_attachment=True, download_name=filename)


@admin_bp.route("/api/export/leads", methods=["GET"])
@login_required
def api_export_leads():
    fmt = request.args.get("format", "csv")
    status = request.args.get("status")
    rows = db.list_leads(status=status, limit=10000)
    try:
        content, mimetype, filename = exports.export_leads(rows, fmt)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return send_file(io.BytesIO(content), mimetype=mimetype, as_attachment=True, download_name=filename)


# ---------------------------------------------------------------------------
# Settings (widget appearance)
# ---------------------------------------------------------------------------

DEFAULT_WIDGET_THEME = "professional_navy"
WIDGET_THEMES = {
    "professional_navy": {"label": "Professional Navy", "swatch": ["#101828", "#3f6fb0"]},
    "charcoal_gold": {"label": "Charcoal & Gold", "swatch": ["#17171a", "#e0bb5c"]},
    "corporate_blue": {"label": "Corporate Blue", "swatch": ["#0b1f3f", "#2f6fed"]},
    "slate_teal": {"label": "Slate & Teal", "swatch": ["#16232b", "#2dd4bf"]},
    "deep_green": {"label": "Deep Green", "swatch": ["#0c2018", "#34a853"]},
}


@admin_bp.route("/api/settings/theme", methods=["GET"])
@login_required
def api_get_theme():
    current = db.get_setting("widget_theme", DEFAULT_WIDGET_THEME)
    if current not in WIDGET_THEMES:
        current = DEFAULT_WIDGET_THEME
    return jsonify({
        "current": current,
        "default": DEFAULT_WIDGET_THEME,
        "options": [{"id": k, **v} for k, v in WIDGET_THEMES.items()],
    })


@admin_bp.route("/api/settings/theme", methods=["POST"])
@login_required
def api_set_theme():
    data = request.get_json(force=True) or {}
    theme = data.get("theme", "")
    if theme not in WIDGET_THEMES:
        return jsonify({"error": f"Unknown theme. Choose one of: {', '.join(WIDGET_THEMES)}"}), 400
    db.set_setting("widget_theme", theme)
    return jsonify({"current": theme})


@admin_bp.route("/api/settings/theme/reset", methods=["POST"])
@login_required
def api_reset_theme():
    """The 'go back' button — restores the original default theme."""
    db.set_setting("widget_theme", DEFAULT_WIDGET_THEME)
    return jsonify({"current": DEFAULT_WIDGET_THEME})
