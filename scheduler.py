"""
scheduler.py
Minimal background scheduler - no external cron/task-queue dependency.
Runs a daemon thread that checks once an hour whether today's backup has
been taken yet, and triggers one if not. Started from app.py at startup.
"""

import threading
import time
from datetime import datetime

import backup

_CHECK_INTERVAL_SECONDS = 60 * 60  # hourly check is enough for a "daily" backup


def _todays_backup_exists():
    today = datetime.utcnow().strftime("%Y%m%d")
    return any(b["filename"].startswith(f"bmy_chatbot_{today}") for b in backup.list_backups())


def _loop():
    while True:
        try:
            if not _todays_backup_exists():
                backup.backup_now()
        except Exception:
            pass  # scheduler must never crash the app
        time.sleep(_CHECK_INTERVAL_SECONDS)


def start():
    """Call once at app startup. Safe to call multiple times (each call just
    spins up another daemon thread, but app.py only calls this once)."""
    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    return thread
