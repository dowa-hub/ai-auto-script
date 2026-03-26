"""
Unified script ingestion.

parse_unified(content, filename) does ONE pass through the document and
returns both the word list (for STT tracking) AND the display HTML in a
single result.  Because they're built together, line_index N in the word
list always corresponds to data-line="N" in the HTML — no drift.

PDF / image files return html=None (display is handled natively by the
frontend: PDF.js for PDFs, <img> for images).
"""
import io
import re
import html as _html
from pathlib import Path


# ── Public API ────────────────────────────────────────────────────────────────

def parse_unified(content: bytes, filename: str) -> dict:
    """
    Single-pass parse.  Returns:
      lines       – list of plain-text lines (one per script row/paragraph)
      words       – list of {word, clean, line_index}
      word_count  – int
      line_count  – int
      html        – HTML string with data-line="N" on every block element,
                    or None for PDF / image files
    """
    ext = Path(filename).suffix.lower()

    if ext == ".pdf":
        return _pdf_parse(content)
    if ext in (".docx", ".doc"):
        return _docx_parse(content)
    if ext in (".xlsx", ".xls"):
        return _excel_parse(content)
    if ext in (".txt", ".text"):
        return _txt_parse(content)
    if ext in (".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".webp"):
        return _image_parse(content)
    # Unknown — try plain text
    return _txt_parse(content)


# Keep parse_script as a backwards-compatible alias used by main.py
def parse_script(content: bytes, filename: str) -> dict:
    result = parse_unified(content, filename)
    return result


# ── Per-format parsers (each does one pass, builds words + HTML together) ─────

def _docx_parse(content: bytes) -> dict:
    from docx import Document
    lines, words, html_rows = [], [], []

    doc = Document(io.BytesIO(content))
    for child in doc.element.body:
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag

        if tag == "p":
            text = "".join(
                r.text or "" for r in child.iter() if r.tag.split("}")[-1] == "t"
            )
            if not text.strip():
                continue
            line_idx = _add_line(lines, words, text)
            html_rows.append(f'<p data-line="{line_idx}">{_h(text)}</p>')

        elif tag == "tbl":
            html_rows.append("<table>")
            for tr in child.iter():
                if tr.tag.split("}")[-1] != "tr":
                    continue
                cells, seen = [], set()
                for tc in tr.iter():
                    if tc.tag.split("}")[-1] != "tc":
                        continue
                    ct = "".join(
                        r.text or "" for r in tc.iter() if r.tag.split("}")[-1] == "t"
                    ).strip()
                    if ct not in seen:
                        cells.append(ct)
                        seen.add(ct)
                row_text = "\t".join(cells)
                if not row_text.strip():
                    continue
                line_idx = _add_line(lines, words, row_text)
                tds = "".join(f"<td>{_h(c)}</td>" for c in cells)
                html_rows.append(f'<tr data-line="{line_idx}">{tds}</tr>')
            html_rows.append("</table>")

    return _result(lines, words, "\n".join(html_rows))


def _excel_parse(content: bytes) -> dict:
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    lines, words, html_rows = [], [], []

    for ws in wb.worksheets:
        html_rows.append(f'<h3 class="sheet-name">{_h(ws.title)}</h3><table>')
        for row in ws.iter_rows(values_only=True):
            # Keep cells that are not None; skip rows that are entirely empty
            cells = [c for c in row if c is not None]
            row_text = "\t".join(str(c) for c in cells if str(c).strip())
            if not row_text.strip():
                continue
            line_idx = _add_line(lines, words, row_text)
            tds = "".join(f"<td>{_h(str(c))}</td>" for c in cells)
            html_rows.append(f'<tr data-line="{line_idx}">{tds}</tr>')
        html_rows.append("</table>")

    return _result(lines, words, "\n".join(html_rows))


def _txt_parse(content: bytes) -> dict:
    lines, words, html_rows = [], [], []
    for raw in content.decode("utf-8", errors="replace").split("\n"):
        if not raw.strip():
            continue
        line_idx = _add_line(lines, words, raw)
        html_rows.append(f'<p data-line="{line_idx}">{_h(raw)}</p>')
    return _result(lines, words, "\n".join(html_rows))


def _pdf_parse(content: bytes) -> dict:
    try:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(content)

        # Detect "Script" column boundaries for table-based PDFs
        script_col = _detect_script_column(pdf)

        pages = []
        for page in pdf:
            textpage = page.get_textpage()
            if script_col:
                left, right = script_col
                text = textpage.get_text_bounded(
                    left=left, bottom=0,
                    right=right, top=page.get_height(),
                )
            else:
                text = textpage.get_text_bounded()
            pages.append(text)

        text = "\n".join(pages)
        if len(text.strip()) < 100:
            text = _ocr_pdf(content)
    except Exception as e:
        raise ValueError(f"PDF parse failed: {e}")

    lines, words = [], []
    for raw in text.split("\n"):
        if raw.strip():
            _add_line(lines, words, raw)
    return _result(lines, words, None)  # HTML=None → PDF.js handles display


def _detect_script_column(pdf):
    """Detect the 'Script' column boundaries in a table-formatted PDF.

    Looks for column header row (Time | Script | Virtual Cues | ...) and uses
    the header x-positions to determine the Script column's left/right bounds.

    Returns (left_x, right_x) in PDF coordinates, or None if no table detected.
    Falls back gracefully so non-table PDFs are unaffected.
    """
    try:
        # Scan first few pages to find column headers
        for page_idx in range(min(len(pdf), 3)):
            page = pdf[page_idx]
            tp = page.get_textpage()
            n = tp.count_chars()
            if n == 0:
                continue

            limit = min(n, 3000)
            chars = []
            for i in range(limit):
                c = tp.get_text_range(i, 1)
                try:
                    box = tp.get_charbox(i)   # (left, bottom, right, top)
                    chars.append((c, box[0]))  # char, x_left
                except Exception:
                    chars.append((c, 0))

            full = "".join(c for c, _ in chars)

            # Find column headers — we need "Time" and "Virtual" at minimum
            headers = {}
            for hdr in ["Time", "Virtual", "Projection"]:
                # Search for each header, picking the occurrence with lowest x
                # (to avoid matching body text like "Time:" in a script line)
                idx = 0
                best_x = None
                while True:
                    found = full.find(hdr, idx)
                    if found < 0:
                        break
                    x = chars[found][1]
                    if best_x is None or x < best_x:
                        best_x = x
                        headers[hdr] = x
                    idx = found + len(hdr)

            if "Time" not in headers:
                continue

            # Script column: starts after "Time" header, ends at "Virtual" or "Projection"
            # Time column is narrow (~40-80px), Script body starts at ~80-100
            time_x = headers["Time"]
            # Script left = Time position + generous offset to clear the Time column
            script_left = time_x + 40

            # Script right = start of Virtual Cues or Projection column
            script_right = None
            for col in ["Virtual", "Projection"]:
                if col in headers:
                    script_right = headers[col]
                    break

            if script_right and script_right > script_left:
                print(f"[PDF] Detected Script column: x={script_left:.0f} → {script_right:.0f}")
                return (script_left, script_right)

        return None
    except Exception:
        return None


def _image_parse(content: bytes) -> dict:
    try:
        from PIL import Image
        import pytesseract
        text = pytesseract.image_to_string(Image.open(io.BytesIO(content)))
    except Exception:
        text = ""
    lines, words = [], []
    for raw in text.split("\n"):
        if raw.strip():
            _add_line(lines, words, raw)
    return _result(lines, words, None)  # HTML=None → <img> handles display


# ── Helpers ───────────────────────────────────────────────────────────────────

def _add_line(lines: list, words: list, text: str) -> int:
    """Append a line and its words; return the new line_index."""
    line_idx = len(lines)
    lines.append(text)
    for token in re.findall(r"\S+", text):
        words.append({
            "word": token,
            "clean": re.sub(r"[^\w']", "", token.lower()),
            "line_index": line_idx,
        })
    return line_idx


def _result(lines: list, words: list, html) -> dict:
    return {
        "lines": lines,
        "words": words,
        "word_count": len(words),
        "line_count": len(lines),
        "html": html,
    }


def _h(text: str) -> str:
    return _html.escape(str(text))


def _ocr_pdf(content: bytes) -> str:
    try:
        from pdf2image import convert_from_bytes
        images = convert_from_bytes(content)
        from PIL import Image
        import pytesseract
        return "\n".join(pytesseract.image_to_string(img) for img in images)
    except Exception:
        return ""
