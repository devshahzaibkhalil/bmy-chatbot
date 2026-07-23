"""
database/db.py
SQLite storage layer for the BMY Marketer AI Assistant.

Day-1 scope: schema for the full CRM (customers, conversations, messages,
leads, appointments, files, admin_users, analytics) is created up front so
later days can build on it without migrations. Read/write helpers are
implemented for the pieces the Day-1 chatbot actually uses: customers,
conversations, and messages. Lead automation, admin auth, exports, and
analytics rollups land in later days.
"""

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime

from config import Config
import crypto_utils

# Customer PII fields encrypted at rest (see crypto_utils.py)
_CUSTOMER_PII_FIELDS = ("full_name", "email", "phone", "company_name", "website_url")
_LEAD_PII_FIELDS = ("name", "email", "phone", "company_name", "conversation_summary")


def _decrypt_fields(row, fields):
    """Decrypts the given keys in place on a dict (skips missing/None keys)."""
    if row is None:
        return row
    for field in fields:
        if field in row and row[field] is not None:
            row[field] = crypto_utils.decrypt(row[field])
    return row

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id TEXT PRIMARY KEY,
    full_name TEXT,
    email TEXT,
    phone TEXT,
    company_name TEXT,
    website_url TEXT,
    country TEXT,
    ip_address TEXT,
    browser_info TEXT,
    device_info TEXT,
    email_index TEXT,
    phone_index TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    customer_id TEXT REFERENCES customers(id),
    session_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    duration_seconds INTEGER,
    status TEXT NOT NULL DEFAULT 'open',   -- open, closed, escalated
    interested_service TEXT,
    budget TEXT,
    timeline TEXT,
    pending_pricing_topic TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT REFERENCES conversations(id),
    sender TEXT NOT NULL,                  -- customer, bot
    message TEXT NOT NULL,
    matched_faq_id TEXT,
    match_score INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS leads (
    id TEXT PRIMARY KEY,
    customer_id TEXT REFERENCES customers(id),
    conversation_id TEXT REFERENCES conversations(id),
    name TEXT,
    email TEXT,
    phone TEXT,
    company_name TEXT,
    interested_service TEXT,
    budget TEXT,
    timeline TEXT,
    conversation_summary TEXT,
    status TEXT NOT NULL DEFAULT 'new',    -- new, contacted, proposal_sent, won, lost
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS appointments (
    id TEXT PRIMARY KEY,
    customer_id TEXT REFERENCES customers(id),
    conversation_id TEXT REFERENCES conversations(id),
    scheduled_for TEXT,
    notes TEXT,
    status TEXT NOT NULL DEFAULT 'requested',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY,
    customer_id TEXT REFERENCES customers(id),
    conversation_id TEXT REFERENCES conversations(id),
    filename TEXT,
    stored_path TEXT,
    file_type TEXT,
    size_bytes INTEGER,
    purpose TEXT,                          -- customer_upload, knowledge_base
    extraction_status TEXT,                -- pending, extracted, failed, unsupported
    uploaded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_documents (
    id TEXT PRIMARY KEY,
    file_id TEXT REFERENCES files(id),
    chunk_index INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_users (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'admin',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analytics_events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,              -- new_conversation, new_lead, quote_request, unanswered_question, etc.
    conversation_id TEXT,
    payload TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS internal_notes (
    id TEXT PRIMARY KEY,
    conversation_id TEXT REFERENCES conversations(id),
    admin_username TEXT,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT,
    conversation_id TEXT,
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def now_iso():
    return datetime.utcnow().isoformat()


def new_id():
    return str(uuid.uuid4())


@contextmanager
def get_conn():
    conn = sqlite3.connect(Config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        # Lightweight migrations for columns added after the initial schema.
        for statement in [
            "ALTER TABLE conversations ADD COLUMN deleted_at TEXT",
            "ALTER TABLE customers ADD COLUMN email_index TEXT",
            "ALTER TABLE customers ADD COLUMN phone_index TEXT",
            "ALTER TABLE conversations ADD COLUMN pending_pricing_topic TEXT",
            "ALTER TABLE conversations ADD COLUMN lead_flow_step INTEGER",
            "ALTER TABLE conversations ADD COLUMN lead_flow_answers TEXT",
        ]:
            try:
                conn.execute(statement)
            except sqlite3.OperationalError:
                pass  # column already exists


# ---------- Customers ----------

def find_customer_by_contact(email=None, phone=None):
    """Look up a returning customer by email or phone (whichever is provided).
    Looks up via the blind index since email/phone are encrypted at rest."""
    if not email and not phone:
        return None
    with get_conn() as conn:
        if email:
            row = conn.execute(
                "SELECT * FROM customers WHERE email_index = ? ORDER BY last_seen_at DESC LIMIT 1",
                (crypto_utils.blind_index(email),),
            ).fetchone()
            if row:
                return _decrypt_fields(dict(row), _CUSTOMER_PII_FIELDS)
        if phone:
            row = conn.execute(
                "SELECT * FROM customers WHERE phone_index = ? ORDER BY last_seen_at DESC LIMIT 1",
                (crypto_utils.blind_index(phone),),
            ).fetchone()
            if row:
                return _decrypt_fields(dict(row), _CUSTOMER_PII_FIELDS)
    return None


def get_customer(customer_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
        return _decrypt_fields(dict(row), _CUSTOMER_PII_FIELDS) if row else None


def create_customer(**fields):
    customer_id = new_id()
    ts = now_iso()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO customers
               (id, full_name, email, phone, company_name, website_url,
                country, ip_address, browser_info, device_info,
                email_index, phone_index, first_seen_at, last_seen_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                customer_id,
                crypto_utils.encrypt(fields.get("full_name")),
                crypto_utils.encrypt(fields.get("email")),
                crypto_utils.encrypt(fields.get("phone")),
                crypto_utils.encrypt(fields.get("company_name")),
                crypto_utils.encrypt(fields.get("website_url")),
                fields.get("country"),
                fields.get("ip_address"),
                fields.get("browser_info"),
                fields.get("device_info"),
                crypto_utils.blind_index(fields.get("email")),
                crypto_utils.blind_index(fields.get("phone")),
                ts,
                ts,
            ),
        )
    return customer_id


def update_customer(customer_id, **fields):
    if not fields:
        return
    plain_cols = {"country", "ip_address", "browser_info", "device_info"}
    encrypted_cols = set(_CUSTOMER_PII_FIELDS)

    sets, values = [], []
    for k, v in fields.items():
        if not v:
            continue
        if k in encrypted_cols:
            sets.append(f"{k} = ?")
            values.append(crypto_utils.encrypt(v))
            if k == "email":
                sets.append("email_index = ?")
                values.append(crypto_utils.blind_index(v))
            elif k == "phone":
                sets.append("phone_index = ?")
                values.append(crypto_utils.blind_index(v))
        elif k in plain_cols:
            sets.append(f"{k} = ?")
            values.append(v)
    if not sets:
        return
    sets.append("last_seen_at = ?")
    values.append(now_iso())
    values.append(customer_id)
    with get_conn() as conn:
        conn.execute(f"UPDATE customers SET {', '.join(sets)} WHERE id = ?", values)


# ---------- Conversations ----------

def create_conversation(session_id, customer_id=None):
    conv_id = new_id()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO conversations (id, customer_id, session_id, started_at, status)
               VALUES (?, ?, ?, ?, 'open')""",
            (conv_id, customer_id, session_id, now_iso()),
        )
    return conv_id


def get_conversation(conversation_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        return dict(row) if row else None


def get_open_conversation_by_session(session_id, max_age_hours=12):
    """
    Finds the visitor's most recent still-open conversation for this browser
    session, if any, so a page reload or widget reopen can resume it instead
    of always starting a brand-new (empty-looking) conversation. Bounded by
    max_age_hours so a very old open conversation isn't resumed indefinitely.
    """
    with get_conn() as conn:
        row = conn.execute(
            """SELECT * FROM conversations
               WHERE session_id = ? AND status = 'open'
               ORDER BY started_at DESC LIMIT 1""",
            (session_id,),
        ).fetchone()
    if not row:
        return None
    conv = dict(row)
    started = datetime.fromisoformat(conv["started_at"])
    age_hours = (datetime.utcnow() - started).total_seconds() / 3600
    if age_hours > max_age_hours:
        return None
    return conv


def close_conversation(conversation_id, status="closed"):
    conv = get_conversation(conversation_id)
    if not conv:
        return
    ended_at = now_iso()
    started = datetime.fromisoformat(conv["started_at"])
    duration = int((datetime.utcnow() - started).total_seconds())
    with get_conn() as conn:
        conn.execute(
            """UPDATE conversations
               SET ended_at = ?, duration_seconds = ?, status = ?
               WHERE id = ?""",
            (ended_at, duration, status, conversation_id),
        )


_UNSET = object()


def update_conversation_intent(conversation_id, interested_service=None, budget=None,
                                timeline=None, pending_pricing_topic=_UNSET):
    sets, values = [], []
    if interested_service:
        sets.append("interested_service = ?")
        values.append(interested_service)
    if budget:
        sets.append("budget = ?")
        values.append(budget)
    if timeline:
        sets.append("timeline = ?")
        values.append(timeline)
    if pending_pricing_topic is not _UNSET:
        # "" (falsy but not _UNSET) intentionally clears the pending topic
        # once it's been resolved by a follow-up answer.
        sets.append("pending_pricing_topic = ?")
        values.append(pending_pricing_topic or None)
    if not sets:
        return
    values.append(conversation_id)
    with get_conn() as conn:
        conn.execute(f"UPDATE conversations SET {', '.join(sets)} WHERE id = ?", values)


def update_conversation_interested_service(
    conversation_id,
    interested_service
):
    if not conversation_id or not interested_service:
        return

    with get_conn() as conn:
        conn.execute(
            """
            UPDATE conversations
            SET interested_service = ?
            WHERE id = ?
            """,
            (
                interested_service,
                conversation_id,
            ),
        )


def update_conversation_lead_flow(conversation_id, step=_UNSET, answers_json=_UNSET):
    """
    Persists progress through the guided purchase/qualification flow
    (see chat_engine.py's lead-flow handling). `step` is the index of the
    next question to ask, or None once the flow is finished/cancelled -
    both are valid values, so _UNSET (not passed at all) is the sentinel
    for "leave this column alone". `answers_json` is the JSON-encoded
    dict of answers collected so far (or None to clear it).
    """
    sets, values = [], []
    if step is not _UNSET:
        sets.append("lead_flow_step = ?")
        values.append(step)
    if answers_json is not _UNSET:
        sets.append("lead_flow_answers = ?")
        values.append(answers_json)
    if not sets:
        return
    values.append(conversation_id)
    with get_conn() as conn:
        conn.execute(f"UPDATE conversations SET {', '.join(sets)} WHERE id = ?", values)


def get_customer_conversations(customer_id, limit=10):
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM conversations WHERE customer_id = ?
               ORDER BY started_at DESC LIMIT ?""",
            (customer_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------- Messages ----------

def add_message(conversation_id, sender, message, matched_faq_id=None, match_score=None):
    msg_id = new_id()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO messages
               (id, conversation_id, sender, message, matched_faq_id, match_score, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (msg_id, conversation_id, sender, crypto_utils.encrypt(message), matched_faq_id, match_score, now_iso()),
        )
    return msg_id


def get_conversation_messages(conversation_id):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
            (conversation_id,),
        ).fetchall()
        results = []
        for r in rows:
            row = dict(r)
            row["message"] = crypto_utils.decrypt(row["message"])
            results.append(row)
        return results


# ---------- Analytics events (lightweight logging used by app.py) ----------

def log_event(event_type, conversation_id=None, payload=None):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO analytics_events (id, event_type, conversation_id, payload, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (new_id(), event_type, conversation_id, payload, now_iso()),
        )


def update_conversation_status(conversation_id, status):
    """Set status without necessarily closing (used by admin: resolved/escalated/open)."""
    with get_conn() as conn:
        conn.execute("UPDATE conversations SET status = ? WHERE id = ?", (status, conversation_id))


def delete_conversation(conversation_id):
    """Permanent purge - removes messages, notes, and the conversation itself."""
    with get_conn() as conn:
        conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
        conn.execute("DELETE FROM internal_notes WHERE conversation_id = ?", (conversation_id,))
        conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))


def soft_delete_conversation(conversation_id):
    """Marks a conversation as deleted without removing data - recoverable via restore_conversation."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE conversations SET deleted_at = ? WHERE id = ?", (now_iso(), conversation_id)
        )


def restore_conversation(conversation_id):
    with get_conn() as conn:
        conn.execute(
            "UPDATE conversations SET deleted_at = NULL WHERE id = ?", (conversation_id,)
        )


def list_deleted_conversations(limit=50, offset=0):
    query = """
        SELECT c.*, cu.full_name, cu.email, cu.phone, cu.company_name
        FROM conversations c
        LEFT JOIN customers cu ON cu.id = c.customer_id
        WHERE c.deleted_at IS NOT NULL
        ORDER BY c.deleted_at DESC LIMIT ? OFFSET ?
    """
    with get_conn() as conn:
        rows = conn.execute(query, (limit, offset)).fetchall()
        return [_decrypt_fields(dict(r), _CUSTOMER_PII_FIELDS) for r in rows]

# ---------- Admin: conversation search/list/detail ----------

def list_conversations(search=None, status=None, service=None, date_from=None, date_to=None,
                        limit=50, offset=0):
    """
    Admin conversation list with joins to customers. Status/service/date
    filters run in SQL; search runs in Python after decrypting the joined
    customer fields, since email/phone/name/company are encrypted at rest
    and can't be matched with SQL LIKE.
    """
    query = """
        SELECT c.*, cu.full_name, cu.email, cu.phone, cu.company_name
        FROM conversations c
        LEFT JOIN customers cu ON cu.id = c.customer_id
        WHERE c.deleted_at IS NULL
    """
    params = []

    if status:
        query += " AND c.status = ?"
        params.append(status)

    if service:
        query += " AND c.interested_service = ?"
        params.append(service)

    if date_from:
        query += " AND c.started_at >= ?"
        params.append(date_from)

    if date_to:
        query += " AND c.started_at <= ?"
        params.append(date_to)

    query += " ORDER BY c.started_at DESC"
    # When searching, pull a bounded working set and filter/paginate in Python.
    # Otherwise paginate in SQL as usual.
    fetch_limit = 5000 if search else limit
    fetch_offset = 0 if search else offset
    query += " LIMIT ? OFFSET ?"
    params += [fetch_limit, fetch_offset]

    with get_conn() as conn:
        rows = [_decrypt_fields(dict(r), _CUSTOMER_PII_FIELDS) for r in conn.execute(query, params).fetchall()]

    if search:
        needle = search.lower()
        rows = [
            r for r in rows
            if needle in (r.get("full_name") or "").lower()
            or needle in (r.get("email") or "").lower()
            or needle in (r.get("phone") or "").lower()
            or needle in (r.get("company_name") or "").lower()
        ]
        rows = rows[offset:offset + limit]

    return rows


def get_conversation_detail(conversation_id):
    conv = get_conversation(conversation_id)
    if not conv:
        return None
    with get_conn() as conn:
        customer_row = None
        if conv.get("customer_id"):
            customer_row = conn.execute(
                "SELECT * FROM customers WHERE id = ?", (conv["customer_id"],)
            ).fetchone()
        notes = conn.execute(
            "SELECT * FROM internal_notes WHERE conversation_id = ? ORDER BY created_at ASC",
            (conversation_id,),
        ).fetchall()
    conv["customer"] = _decrypt_fields(dict(customer_row), _CUSTOMER_PII_FIELDS) if customer_row else None
    conv["messages"] = get_conversation_messages(conversation_id)
    conv["notes"] = [dict(n) for n in notes]
    return conv


def add_internal_note(conversation_id, note, admin_username=None):
    note_id = new_id()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO internal_notes (id, conversation_id, admin_username, note, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (note_id, conversation_id, admin_username, note, now_iso()),
        )
    return note_id


# ---------- Leads ----------

def create_lead(**fields):
    lead_id = new_id()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO leads
               (id, customer_id, conversation_id, name, email, phone, company_name,
                interested_service, budget, timeline, conversation_summary, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?)""",
            (
                lead_id,
                fields.get("customer_id"),
                fields.get("conversation_id"),
                crypto_utils.encrypt(fields.get("name")),
                crypto_utils.encrypt(fields.get("email")),
                crypto_utils.encrypt(fields.get("phone")),
                crypto_utils.encrypt(fields.get("company_name")),
                fields.get("interested_service"),
                fields.get("budget"),
                fields.get("timeline"),
                crypto_utils.encrypt(fields.get("conversation_summary")),
                now_iso(),
            ),
        )
    return lead_id


def lead_exists_for_conversation(conversation_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM leads WHERE conversation_id = ? LIMIT 1", (conversation_id,)
        ).fetchone()
        return row is not None


def list_leads(status=None, search=None, limit=50, offset=0):
    """name/email/phone/company_name are encrypted at rest, so search
    decrypts and filters in Python rather than using SQL LIKE."""
    query = "SELECT * FROM leads WHERE 1=1"
    params = []
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY created_at DESC"

    fetch_limit = 5000 if search else limit
    fetch_offset = 0 if search else offset
    query += " LIMIT ? OFFSET ?"
    params += [fetch_limit, fetch_offset]

    with get_conn() as conn:
        rows = [_decrypt_fields(dict(r), _LEAD_PII_FIELDS) for r in conn.execute(query, params).fetchall()]

    if search:
        needle = search.lower()
        rows = [
            r for r in rows
            if needle in (r.get("name") or "").lower()
            or needle in (r.get("email") or "").lower()
            or needle in (r.get("phone") or "").lower()
            or needle in (r.get("company_name") or "").lower()
        ]
        rows = rows[offset:offset + limit]

    return rows


def update_lead_status(lead_id, status):
    with get_conn() as conn:
        conn.execute("UPDATE leads SET status = ? WHERE id = ?", (status, lead_id))


# ---------- Admin users ----------

def create_admin_user(username, password_hash, role="admin"):
    user_id = new_id()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO admin_users (id, username, password_hash, role, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, username, password_hash, role, now_iso()),
        )
    return user_id


def get_admin_by_username(username):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM admin_users WHERE username = ?", (username,)
        ).fetchone()
        return dict(row) if row else None


def list_admin_users():
    """Excludes password_hash - never return it to the frontend."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, username, role, created_at FROM admin_users ORDER BY created_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def delete_admin_user(user_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM admin_users WHERE id = ?", (user_id,))


def any_admin_exists():
    with get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) as c FROM admin_users").fetchone()
        return row["c"] > 0


# ---------- Analytics dashboard ----------

def get_analytics_summary():
    with get_conn() as conn:
        total_conversations = conn.execute("SELECT COUNT(*) c FROM conversations").fetchone()["c"]
        active_conversations = conn.execute(
            "SELECT COUNT(*) c FROM conversations WHERE status = 'open'"
        ).fetchone()["c"]
        total_customers = conn.execute("SELECT COUNT(*) c FROM customers").fetchone()["c"]
        new_leads = conn.execute(
            "SELECT COUNT(*) c FROM leads WHERE status = 'new'"
        ).fetchone()["c"]
        returning_customers = conn.execute(
            """SELECT COUNT(*) c FROM (
                 SELECT customer_id FROM conversations
                 WHERE customer_id IS NOT NULL
                 GROUP BY customer_id HAVING COUNT(*) > 1
               )"""
        ).fetchone()["c"]
        avg_duration = conn.execute(
            "SELECT AVG(duration_seconds) a FROM conversations WHERE duration_seconds IS NOT NULL"
        ).fetchone()["a"]
        most_asked = conn.execute(
            """SELECT matched_faq_id, COUNT(*) c FROM messages
               WHERE matched_faq_id IS NOT NULL
               GROUP BY matched_faq_id ORDER BY c DESC LIMIT 5"""
        ).fetchall()
        most_requested_services = conn.execute(
            """SELECT interested_service, COUNT(*) c FROM conversations
               WHERE interested_service IS NOT NULL
               GROUP BY interested_service ORDER BY c DESC LIMIT 5"""
        ).fetchall()
        unanswered = conn.execute(
            "SELECT COUNT(*) c FROM analytics_events WHERE event_type = 'unanswered_question'"
        ).fetchone()["c"]

    return {
        "total_conversations": total_conversations,
        "active_conversations": active_conversations,
        "total_customers": total_customers,
        "new_leads": new_leads,
        "returning_customers": returning_customers,
        "avg_chat_duration_seconds": round(avg_duration, 1) if avg_duration else 0,
        "avg_response_time_seconds": get_average_response_time(),
        "most_asked_faqs": [dict(r) for r in most_asked],
        "most_requested_services": [dict(r) for r in most_requested_services],
        "unanswered_questions": unanswered,
    }


def get_daily_report(days=7):
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT substr(started_at, 1, 10) as day, COUNT(*) as conversations
               FROM conversations
               GROUP BY day ORDER BY day DESC LIMIT ?""",
            (days,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_weekly_report(weeks=8):
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT strftime('%Y-W%W', started_at) as week, COUNT(*) as conversations
               FROM conversations
               GROUP BY week ORDER BY week DESC LIMIT ?""",
            (weeks,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_monthly_report(months=12):
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT substr(started_at, 1, 7) as month, COUNT(*) as conversations
               FROM conversations
               GROUP BY month ORDER BY month DESC LIMIT ?""",
            (months,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_average_response_time():
    """
    Average seconds between a customer message and the next bot message
    in the same conversation, across all conversations.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT conversation_id, sender, created_at FROM messages
               ORDER BY conversation_id, created_at ASC"""
        ).fetchall()

    gaps = []
    pending_customer_time = {}
    for row in rows:
        conv_id, sender, created_at = row["conversation_id"], row["sender"], row["created_at"]
        ts = datetime.fromisoformat(created_at)
        if sender == "customer":
            pending_customer_time[conv_id] = ts
        elif sender == "bot" and conv_id in pending_customer_time:
            gap = (ts - pending_customer_time.pop(conv_id)).total_seconds()
            if 0 <= gap < 3600:  # ignore multi-hour gaps (agent replied much later)
                gaps.append(gap)

    if not gaps:
        return 0
    return round(sum(gaps) / len(gaps), 2)


# ---------- Notifications ----------

def create_notification(event_type, title, message=None, conversation_id=None):
    notif_id = new_id()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO notifications
               (id, event_type, title, message, conversation_id, is_read, created_at)
               VALUES (?, ?, ?, ?, ?, 0, ?)""",
            (notif_id, event_type, title, message, conversation_id, now_iso()),
        )
    return notif_id


def list_notifications(unread_only=False, limit=30):
    query = "SELECT * FROM notifications"
    if unread_only:
        query += " WHERE is_read = 0"
    query += " ORDER BY created_at DESC LIMIT ?"
    with get_conn() as conn:
        rows = conn.execute(query, (limit,)).fetchall()
        return [dict(r) for r in rows]


def unread_notification_count():
    with get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) c FROM notifications WHERE is_read = 0").fetchone()
        return row["c"]


def mark_notification_read(notification_id):
    with get_conn() as conn:
        conn.execute("UPDATE notifications SET is_read = 1 WHERE id = ?", (notification_id,))


def mark_all_notifications_read():
    with get_conn() as conn:
        conn.execute("UPDATE notifications SET is_read = 1 WHERE is_read = 0")


# ---------- Appointments ----------

def create_appointment(customer_id=None, conversation_id=None, scheduled_for=None, notes=None):
    appt_id = new_id()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO appointments
               (id, customer_id, conversation_id, scheduled_for, notes, status, created_at)
               VALUES (?, ?, ?, ?, ?, 'requested', ?)""",
            (appt_id, customer_id, conversation_id, scheduled_for, notes, now_iso()),
        )
    return appt_id


def list_appointments(status=None, limit=50, offset=0):
    query = """
        SELECT a.*, cu.full_name, cu.email, cu.phone
        FROM appointments a
        LEFT JOIN customers cu ON cu.id = a.customer_id
        WHERE 1=1
    """
    params = []
    if status:
        query += " AND a.status = ?"
        params.append(status)
    query += " ORDER BY a.created_at DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
        return [_decrypt_fields(dict(r), _CUSTOMER_PII_FIELDS) for r in rows]


def update_appointment_status(appointment_id, status):
    with get_conn() as conn:
        conn.execute("UPDATE appointments SET status = ? WHERE id = ?", (status, appointment_id))


# ---------- Files & knowledge documents ----------

def create_file_record(filename, stored_path, file_type=None, size_bytes=None,
                        purpose="customer_upload", customer_id=None, conversation_id=None,
                        extraction_status="pending"):
    file_id = new_id()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO files
               (id, customer_id, conversation_id, filename, stored_path, file_type,
                size_bytes, purpose, extraction_status, uploaded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (file_id, customer_id, conversation_id, filename, stored_path, file_type,
             size_bytes, purpose, extraction_status, now_iso()),
        )
    return file_id


def update_file_extraction_status(file_id, status):
    with get_conn() as conn:
        conn.execute("UPDATE files SET extraction_status = ? WHERE id = ?", (status, file_id))


def list_files(purpose=None, conversation_id=None, limit=50, offset=0):
    query = """
        SELECT f.*, cu.full_name, cu.email
        FROM files f
        LEFT JOIN customers cu ON cu.id = f.customer_id
        WHERE 1=1
    """
    params = []
    if purpose:
        query += " AND f.purpose = ?"
        params.append(purpose)
    if conversation_id:
        query += " AND f.conversation_id = ?"
        params.append(conversation_id)
    query += " ORDER BY f.uploaded_at DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
        return [_decrypt_fields(dict(r), _CUSTOMER_PII_FIELDS) for r in rows]


def get_file(file_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        return dict(row) if row else None


def delete_file_record(file_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM knowledge_documents WHERE file_id = ?", (file_id,))
        conn.execute("DELETE FROM files WHERE id = ?", (file_id,))


def add_knowledge_chunks(file_id, chunks):
    with get_conn() as conn:
        for i, chunk in enumerate(chunks):
            conn.execute(
                """INSERT INTO knowledge_documents (id, file_id, chunk_index, chunk_text, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (new_id(), file_id, i, chunk, now_iso()),
            )


def get_all_knowledge_chunks():
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT kd.*, f.filename FROM knowledge_documents kd
               JOIN files f ON f.id = kd.file_id
               ORDER BY kd.created_at ASC"""
        ).fetchall()
        return [dict(r) for r in rows]


# ---------- Settings (key/value site preferences, e.g. active widget theme) ----------

def get_setting(key, default=None):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
            (key, value, now_iso()),
        )