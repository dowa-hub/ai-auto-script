"""
Unified script ingestion — handles PDF, DOCX, XLSX, plain text, and images.
Returns a normalized dict with lines[] and words[] for the tracker.
"""
import io
import re
from pathlib import Path


def parse_script(content: bytes, filename: str) -> dict:
    ext = Path(filename).suffix.lower()

    if ext == ".pdf":
        text = _parse_pdf(content)
    elif ext in (".docx", ".doc"):
        text = _parse_docx(content)
    elif ext in (".xlsx", ".xls"):
        text = _parse_excel(content)
    elif ext in (".txt", ".text"):
        text = content.decode("utf-8", errors="replace")
    elif ext in (".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".webp"):
        text = _parse_image(content)
    else:
        # Try plain text as a last resort
        text = content.decode("utf-8", errors="replace")

    return _normalize(text)


# ── Parsers ──────────────────────────────────────────────────────────────────

def _parse_pdf(content: bytes) -> str:
    try:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(content)
        pages = []
        for page in pdf:
            textpage = page.get_textpage()
            pages.append(textpage.get_text_range())
        text = "\n".join(pages)
        # If the PDF appears to be scanned (very little text), try OCR
        if len(text.strip()) < 100:
            text = _ocr_pdf(content)
        return text
    except Exception as e:
        raise ValueError(f"PDF parse failed: {e}")


def _ocr_pdf(content: bytes) -> str:
    """Fallback OCR for scanned PDFs — requires poppler + tesseract."""
    try:
        from pdf2image import convert_from_bytes
        images = convert_from_bytes(content)
        pages = [_parse_image(_img_to_bytes(img)) for img in images]
        return "\n".join(pages)
    except Exception:
        return ""


def _img_to_bytes(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _parse_docx(content: bytes) -> str:
    from docx import Document
    doc = Document(io.BytesIO(content))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def _parse_excel(content: bytes) -> str:
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    lines = []
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            row_text = "  ".join(str(c) for c in row if c is not None and str(c).strip())
            if row_text.strip():
                lines.append(row_text)
    return "\n".join(lines)


def _parse_image(content: bytes) -> str:
    from PIL import Image
    import pytesseract
    img = Image.open(io.BytesIO(content))
    return pytesseract.image_to_string(img)


# ── Normalizer ───────────────────────────────────────────────────────────────

def _normalize(text: str) -> dict:
    """
    Break text into lines and a flat word list.
    Each word carries its line index so the UI can scroll to the right line.
    """
    raw_lines = text.split("\n")
    lines = []
    words = []

    for raw_line in raw_lines:
        line = raw_line.strip()
        if not line:
            continue
        line_idx = len(lines)
        lines.append(line)

        for token in re.findall(r"\S+", line):
            clean = re.sub(r"[^\w']", "", token.lower())
            words.append({
                "word": token,
                "clean": clean,
                "line_index": line_idx,
            })

    return {
        "lines": lines,
        "words": words,
        "word_count": len(words),
        "line_count": len(lines),
    }
