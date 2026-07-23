"""
file_manager.py
Saves uploaded files to uploads/documents and, for PDFs, extracts text and
stores it as searchable chunks in the knowledge_documents table.
"""

import os
import uuid

from config import Config
from database import db
import pdf_tools

ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".txt", ".png", ".jpg", ".jpeg"}
MAX_FILE_SIZE_BYTES = 15 * 1024 * 1024  # 15 MB


def is_allowed(filename):
    ext = os.path.splitext(filename)[1].lower()
    return ext in ALLOWED_EXTENSIONS


def save_upload(file_storage, purpose="customer_upload", customer_id=None, conversation_id=None):
    """
    file_storage: a Flask/Werkzeug FileStorage object (from request.files).
    Returns the created file record dict, or raises ValueError on validation failure.
    """
    filename = file_storage.filename
    if not filename or not is_allowed(filename):
        raise ValueError(f"Unsupported file type. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}")

    ext = os.path.splitext(filename)[1].lower()
    safe_name = f"{uuid.uuid4()}{ext}"
    stored_path = os.path.join(Config.UPLOADS_DIR, safe_name)

    os.makedirs(Config.UPLOADS_DIR, exist_ok=True)
    file_storage.save(stored_path)

    size_bytes = os.path.getsize(stored_path)
    if size_bytes > MAX_FILE_SIZE_BYTES:
        os.remove(stored_path)
        raise ValueError("File exceeds the 15 MB upload limit.")

    file_id = db.create_file_record(
        filename=filename,
        stored_path=stored_path,
        file_type=ext.lstrip("."),
        size_bytes=size_bytes,
        purpose=purpose,
        customer_id=customer_id,
        conversation_id=conversation_id,
        extraction_status="pending",
    )

    if ext == ".pdf":
        _extract_and_index(file_id, stored_path)
    else:
        db.update_file_extraction_status(file_id, "unsupported")

    return db.get_file(file_id)


def _extract_and_index(file_id, stored_path):
    text = pdf_tools.extract_text(stored_path)
    if not text:
        db.update_file_extraction_status(file_id, "failed")
        return
    chunks = pdf_tools.chunk_text(text)
    if chunks:
        db.add_knowledge_chunks(file_id, chunks)
        db.update_file_extraction_status(file_id, "extracted")
    else:
        db.update_file_extraction_status(file_id, "failed")
