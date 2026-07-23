"""
pdf_tools.py
Extracts text from uploaded PDFs using PyPDF2 (local, no external API) and
splits it into search-sized chunks for the knowledge base.
"""

from PyPDF2 import PdfReader

MAX_CHUNK_CHARS = 800


def extract_text(pdf_path):
    """Returns the full extracted text of a PDF, or '' if extraction fails."""
    try:
        reader = PdfReader(pdf_path)
        pages = []
        for page in reader.pages:
            text = page.extract_text() or ""
            if text.strip():
                pages.append(text)
        return "\n\n".join(pages)
    except Exception:
        return ""


def chunk_text(text, max_chars=MAX_CHUNK_CHARS):
    """
    Splits text into paragraph-aware chunks no larger than max_chars, so each
    chunk is a reasonably self-contained unit for fuzzy matching later.
    """
    if not text:
        return []

    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    chunks = []
    current = ""

    for para in paragraphs:
        if len(current) + len(para) + 1 <= max_chars:
            current = (current + "\n" + para).strip()
        else:
            if current:
                chunks.append(current)
            if len(para) > max_chars:
                # Very long paragraph - hard split
                for i in range(0, len(para), max_chars):
                    chunks.append(para[i:i + max_chars])
                current = ""
            else:
                current = para

    if current:
        chunks.append(current)

    return chunks
