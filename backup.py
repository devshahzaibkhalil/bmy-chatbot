"""
backup.py
Local, file-based backup for the SQLite database. Copies bmy_chatbot.db
into backups/ with a timestamped filename, keeps a rolling window of the
most recent backups, and can restore from any backup file. No cloud/API
involved - everything stays on disk.
"""

import os
import shutil
from datetime import datetime

from config import Config

MAX_BACKUPS_KEPT = 14  # ~2 weeks of daily backups


def backup_now():
    """Creates a timestamped copy of the live database in backups/."""
    if not os.path.exists(Config.DATABASE_PATH):
        return None

    os.makedirs(Config.BACKUPS_DIR, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(Config.BACKUPS_DIR, f"bmy_chatbot_{timestamp}.db")
    shutil.copy2(Config.DATABASE_PATH, backup_path)
    _rotate_old_backups()
    return backup_path


def _rotate_old_backups():
    backups = list_backups()
    if len(backups) <= MAX_BACKUPS_KEPT:
        return
    for old in backups[MAX_BACKUPS_KEPT:]:
        try:
            os.remove(old["path"])
        except OSError:
            pass


def list_backups():
    """Newest first."""
    if not os.path.isdir(Config.BACKUPS_DIR):
        return []
    entries = []
    for name in os.listdir(Config.BACKUPS_DIR):
        if not name.endswith(".db"):
            continue
        full_path = os.path.join(Config.BACKUPS_DIR, name)
        entries.append({
            "filename": name,
            "path": full_path,
            "size_bytes": os.path.getsize(full_path),
            "created_at": datetime.utcfromtimestamp(os.path.getmtime(full_path)).isoformat(),
        })
    entries.sort(key=lambda e: e["created_at"], reverse=True)
    return entries


def restore_from_backup(filename):
    """
    Restores the live database from a backup file. Takes a safety backup of
    the CURRENT state first, so a restore can itself be undone.
    """
    backup_path = os.path.join(Config.BACKUPS_DIR, filename)
    if not os.path.isfile(backup_path):
        raise FileNotFoundError(f"Backup not found: {filename}")

    # Safety net: back up current state before overwriting it
    backup_now()

    shutil.copy2(backup_path, Config.DATABASE_PATH)
    return True
