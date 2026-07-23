# BMY Marketer AI Assistant

A locally-run AI customer support chatbot for [bmymarketer.com](https://bmymarketer.com/).
No OpenAI/Gemini/paid AI API is used — matching is done entirely with
`fuzzywuzzy` (Levenshtein-based string similarity) against a local JSON
knowledge base.

## Day 1 — what's included

- `app.py` — Flask app: serves the widget and the chat API
- `chat_engine.py` — local NLP/fuzzy-matching engine (no external API)
- `config.py` — paths, matching thresholds, settings
- `database/db.py` — SQLite schema (customers, conversations, messages,
  leads, appointments, files, admin_users, analytics_events) + working
  CRUD for customers/conversations/messages
- `knowledge/website_data.json` — company info, services, pricing,
  contact details (pulled from the live site)
- `knowledge/faq.json` — 15 FAQs from the site + common support questions
- `templates/index.html`, `static/css/style.css`, `static/js/chatbot.js`
  — the chat widget UI

The bot already:
- Greets visitors and recognizes **returning customers** by email/phone
- Answers FAQ, pricing, service, and contact questions from the local KB
- Saves every message (customer + bot) to SQLite with timestamps
- Extracts email/phone mentioned mid-conversation and attaches it to the
  customer record (groundwork for automatic lead capture)
- Flags unanswered questions for follow-up (`analytics_events` table)

## Day 2 — what's new

- `auth.py` — session-based admin login (PBKDF2 password hashing via
  werkzeug, `login_required`/`role_required` decorators)
- `setup_admin.py` — interactive CLI script to create the first admin account
  (`python setup_admin.py`)
- `manage_admin.py` — fast, non-interactive admin management: create an
  account, reset a forgotten password, rename a login, list accounts, or
  delete one — all with a single command (see below)
- `leads.py` — rule-based buying-intent detection (phrases like "get a
  quote", "ready to start", budget/timeline extraction) that automatically
  creates a lead the first time a conversation shows intent
- `admin_routes.py` — the `/admin` blueprint: login, conversation
  search/filter/detail/status/notes/manual-reply/delete, lead list + status
  updates, analytics summary, and export endpoints
- `exports.py` — CSV, JSON, Excel (openpyxl), and PDF (reportlab) export
  for both conversations and leads
- `templates/admin_login.html`, `templates/admin_dashboard.html`,
  `static/css/admin.css`, `static/js/admin.js` — the admin dashboard UI:
  live stats cards, searchable/filterable conversation & lead tables, a
  conversation detail modal (full transcript, status changes, internal
  notes, manual agent reply, delete), and one-click export

To use the dashboard: run `python setup_admin.py` once, then visit
`/admin/login`. Getting "Invalid username or password" or a 401 on login
means no admin account exists yet (or you forgot the password) — fix it
without the interactive prompts:

```
# Create the first admin account
python manage_admin.py create --username admin --password "YourPassword123" --role superadmin

# Forgot the password? Reset it
python manage_admin.py reset-password --username admin --password "NewPassword123"

# Change a username, list accounts, or delete one
python manage_admin.py rename --username admin --new-username owner
python manage_admin.py list
python manage_admin.py delete --username agent1
```

## Day 3 — what's new

- `notifications.py` — creates an in-dashboard notification (bell icon,
  unread count) for every new conversation, new lead, quote request,
  appointment request, and unanswered question. Optional local-SMTP email
  copy if you set `BMY_SMTP_HOST` / `BMY_SMTP_FROM` / `BMY_ADMIN_NOTIFY_EMAIL`
  env vars - off by default, no credentials required to use the dashboard
  notifications themselves.
- `appointments.py` — detects scheduling/consultation requests ("book a
  call", "schedule a consultation", etc.), pulls out a mentioned day/time,
  and creates an appointment (status: requested) for the admin to confirm.
  The chatbot now also replies properly to these instead of falling back
  to "I don't understand."
- `backup.py` + `scheduler.py` — timestamped local backups of the SQLite
  database, a rolling 14-backup window, and a background thread that takes
  one automatically once a day. Restoring a backup takes a safety copy of
  the current state first, so a restore can itself be undone.
- Soft delete + recovery — deleting a conversation from the dashboard now
  marks it `deleted_at` instead of removing it, with a restore endpoint.
  Pass `?permanent=true` to the delete endpoint for a real purge.
- Weekly/monthly analytics reports and average response time (time between
  a customer message and the bot's reply) added to the dashboard.
- Dashboard UI: notification bell with unread badge, an Appointments tab,
  and a Backups tab (run backup now, restore from any snapshot -
  superadmin role required for both).

## Day 4 — what's new

- `pdf_tools.py` — extracts text from PDFs with PyPDF2 and splits it into
  paragraph-aware chunks (no external API)
- `file_manager.py` — handles all file uploads (customer-side and admin
  knowledge-base), validates type/size (15 MB cap), saves to
  `uploads/documents/`, and triggers PDF extraction automatically
- `voice_tools.py` — transcribes uploaded audio to text via
  `SpeechRecognition`. Default engine is the library's free Google Web
  Speech endpoint (no API key/account/billing - same as the package
  default), or set `BMY_VOICE_ENGINE=sphinx` for fully offline transcription
  if you install `pocketsphinx`. **Note:** only WAV/AIFF/FLAC are readable
  directly - browsers usually record webm/ogg via MediaRecorder, so convert
  client-side or with ffmpeg before calling this in production.
- Chatbot can now answer from admin-uploaded PDFs: `chat_engine.py` falls
  back to a fuzzy search over extracted document chunks when no FAQ matches
  well, and cites the source filename in its reply.
- New endpoints: `POST /api/chat/upload` (customer attaches a file mid-chat),
  `POST /api/chat/voice` (customer sends a voice message, transcribed and
  run through the normal chat pipeline), `POST/GET/DELETE
  /admin/api/knowledge/documents` (manage the PDF knowledge base), `GET
  /admin/api/files` (see everything customers have uploaded)
- Chat widget got a 📎 attach button; admin dashboard got a Documents tab
  (upload/delete knowledge-base PDFs, view customer uploads)

Tested end-to-end: uploaded a PDF with a refund policy the FAQ doesn't
cover, confirmed the chatbot found and quoted it with a source citation;
confirmed customer file uploads are saved, notify the admin, and show up
in the dashboard; confirmed the voice endpoint fails gracefully (clear
error message, no crash) on invalid audio.

## Day 5 — what's new (security & production hardening)

- **Encryption at rest**: customer PII (name, email, phone, company, website)
  and the same fields on leads are now encrypted in the SQLite file with
  Fernet (AES-128-CBC + HMAC) via `crypto_utils.py`. Verified directly
  against the raw `.db` file - the values are ciphertext, not plaintext.
  Returning-customer lookup still works via a separate HMAC "blind index"
  (a deterministic fingerprint of the normalized email/phone, not
  reversible) since encrypted values can't be searched directly. Admin
  dashboard search now decrypts and filters in Python instead of SQL
  `LIKE`, so partial-match search still works.
- **Key management**: `SECRET_KEY`, the encryption key, and the blind-index
  key are auto-generated on first run and persisted to
  `database/.keys/` (gitignored) so restarts keep working. Set
  `SECRET_KEY`, `BMY_ENCRYPTION_KEY`, `BMY_BLIND_INDEX_KEY` as environment
  variables instead for anything beyond a single machine - every process
  needs the same keys to decrypt each other's data.
- **Three-tier roles**: `agent` (view conversations/leads/appointments,
  reply, add notes, soft-delete) / `admin` (+ manage leads, knowledge base,
  permanently delete) / `superadmin` (+ backups, team management). Enforced
  server-side on every restricted endpoint, not just hidden in the UI.
  Superadmins can create/remove team accounts from a new Team tab instead
  of only via the CLI script.
- **Rate limiting**: public chat endpoints (start/message/upload/voice) and
  admin login are capped per-IP (`rate_limit.py`, in-memory, no external
  dependency) - default 30 requests/minute, tunable via
  `BMY_RATE_LIMIT_PER_MINUTE`. Verified it returns 429 after the threshold.
- **Production WSGI server**: `wsgi.py` runs the app through `waitress`
  (pure-Python, Windows-friendly) instead of Flask's dev server, which
  explicitly warns against production use. Put nginx/IIS/Caddy in front for
  TLS - waitress only serves plain HTTP.
- `.gitignore` added so databases, encryption keys, backups, and uploaded
  files never accidentally get committed.

Tested end-to-end: confirmed encrypted fields are unreadable in the raw
`.db` file, confirmed returning-customer recognition and admin search both
still work correctly against encrypted data, confirmed an `agent` account
gets a 403 on knowledge-base upload/backups/permanent-delete/creating other
admins while still being able to soft-delete and reply, confirmed the rate
limiter returns 429 after 30 requests/minute, confirmed `wsgi.py` serves
the app correctly through waitress.

## Day 6 — what's new (message encryption, scale-out, deployment)

- Chat message content is now encrypted at rest too (not just customer
  contact fields) - verified directly against the raw `.db` file. Decrypts
  transparently for the admin dashboard and exports.
- `rate_limit.py` now supports Redis as an optional backend
  (`BMY_REDIS_URL`) for when you run more than one app process/worker - an
  in-memory counter alone can't see requests handled by other processes.
  Falls back to in-memory automatically if Redis isn't configured or
  isn't reachable, so this is safe to leave alone until you need it.
- **Deployment package**: `Dockerfile` + `docker-compose.yml`,
  `deploy/nginx.conf.example` (TLS termination),
  `deploy/bmy-chatbot.service` (systemd unit), and `DEPLOYMENT.md` walking
  through Windows (waitress + NSSM), Linux (systemd + nginx + certbot), and
  Docker, plus a pre-launch checklist.

Tested end-to-end: confirmed message text is unreadable in the raw `.db`
file and decrypts correctly in the dashboard; confirmed the rate limiter
still works normally with no Redis configured (falls back silently, no
errors); syntax-validated every changed module.

## Day 7 — what's new (premium UI redesign)

The chat widget got a full visual redesign — same backend, new front end.

- **Design system**: a cool "brand" gradient (navy → indigo → violet → cyan)
  for structure/header/welcome screen, and a separate warm "action" gradient
  (coral → rose) reserved for primary calls-to-action, so the multi-color
  look stays purposeful rather than random. Typography pairs Sora
  (headlines/brand) with Inter (everything else — optimized for legibility
  at chat-bubble sizes).
- **Welcome screen**: shown the first time the widget opens on a page visit.
  An original gradient monogram mark (a "B" glyph with a small coral spark —
  not a reproduction of BMyMarketer's actual trademarked logo, since I can't
  reproduce copyrighted assets, but built in the same spirit), a slow-drifting
  "aurora" gradient-mesh background (the one deliberately bold/signature
  element — respects `prefers-reduced-motion`), a short intro, four quick
  action chips (Our Services / Pricing / Contact Us / Book a Call) that jump
  straight into that topic, and a gradient "Start Chat" button. Reopening the
  widget mid-conversation skips straight back to the thread instead of
  re-showing the welcome screen.
- **Chat thread**: redesigned message bubbles (customer messages use the
  brand gradient), animated message-in transitions, a restyled typing
  indicator, a circular send button, and a pulsing gradient launcher button.
- Fully responsive — verified with Playwright screenshots at both desktop
  (900×800) and mobile (390×844) viewports, plus keyboard focus rings and
  reduced-motion support for accessibility.

Tested end-to-end with real screenshots (not just code review): confirmed
the welcome screen renders correctly, confirmed clicking a quick action
transitions to the chat thread and gets a real bot response, confirmed the
mobile layout doesn't clip or overflow.

## Optional next steps

- If you outgrow SQLite, `database/db.py` is the only file that talks to
  the database - that's the one place to change
- Multi-region/multi-instance deployments would need the key env vars
  (`SECRET_KEY`, `BMY_ENCRYPTION_KEY`, `BMY_BLIND_INDEX_KEY`) synced across
  instances, plus `BMY_REDIS_URL` - both already supported, just need
  values set

## Setup

```bash
cd BMY-Marketer-Chatbot
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS/Linux

pip install -r requirements.txt
python app.py
```

Open `http://localhost:5000` — the chat bubble is in the bottom-right corner.

For production, run through waitress instead of the Flask dev server:

```bash
python wsgi.py
```

and put a reverse proxy (nginx, IIS, Caddy) in front for HTTPS. Set
`SECRET_KEY`, `BMY_ENCRYPTION_KEY`, and `BMY_BLIND_INDEX_KEY` as environment
variables if you're running more than one instance/process (they're
auto-generated and saved locally otherwise, which only works for a single
machine).

> Note: `python-Levenshtein` speeds up fuzzywuzzy but needs a C compiler on
> some systems. If it fails to install on Windows, `pip install fuzzywuzzy`
> alone still works (just slightly slower matching) — remove that one line
> from `requirements.txt` if needed.

## Project structure

```
BMY-Marketer-Chatbot/
├── app.py
├── wsgi.py
├── chat_engine.py
├── config.py
├── crypto_utils.py
├── rate_limit.py
├── auth.py
├── leads.py
├── appointments.py
├── notifications.py
├── backup.py
├── scheduler.py
├── pdf_tools.py
├── voice_tools.py
├── file_manager.py
├── exports.py
├── admin_routes.py
├── setup_admin.py
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── DEPLOYMENT.md
├── .gitignore
├── deploy/
│   ├── nginx.conf.example
│   └── bmy-chatbot.service
├── database/
│   ├── __init__.py
│   ├── db.py
│   ├── .keys/                 (auto-generated encryption/session keys)
│   └── bmy_chatbot.db        (created on first run)
├── knowledge/
│   ├── website_data.json
│   └── faq.json
├── uploads/
│   └── documents/             (customer + knowledge-base file uploads)
├── backups/                   (timestamped .db snapshots)
├── templates/
│   ├── index.html
│   ├── admin_login.html
│   └── admin_dashboard.html
└── static/
    ├── css/style.css
    ├── css/admin.css
    ├── js/chatbot.js
    └── js/admin.js
```

`database/` and `templates/` are additions to the originally requested
structure — needed for SQLite storage and Flask's `render_template`,
respectively. Everything else matches the requested layout.
