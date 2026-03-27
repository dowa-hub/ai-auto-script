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
            # OCR fallback — treat entire result as a single "page"
            pages = [_ocr_pdf(content)]
    except Exception as e:
        raise ValueError(f"PDF parse failed: {e}")

    # Build word list per page so we can return a line_index → page_num mapping
    lines, words = [], []
    line_pages: dict[int, int] = {}  # line_index → 0-based page number

    for page_num, page_text in enumerate(pages):
        for raw in page_text.split("\n"):
            if raw.strip():
                line_idx = _add_line(lines, words, raw)
                line_pages[line_idx] = page_num

    result = _result(lines, words, None)  # HTML=None → PDF.js handles display
    result["line_pages"] = line_pages
    return result


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


def _detect_sections(lines: list, words: list) -> list:
    """Detect meaningful navigation points for the operator.

    Detects in order of priority:
    1. Timestamp-prefixed lines (event rundowns): "650p Emcee: Welcome"
    2. Speaker cue lines (speaker scripts): "Kelly Russell:" alone on a line
    3. ALL CAPS phase headers: "START OF PROGRAM", "PANEL DISCUSSION"

    Each section includes a one-sentence summary extracted from its content.
    """
    line_to_word = {}
    for i, w in enumerate(words):
        li = w["line_index"]
        if li not in line_to_word:
            line_to_word[li] = i

    # Page header / AV-cue noise — skip entirely
    _NOISE = re.compile(
        r'^(Script\s*$|H\s*[–—\-]|for AV Team:|script to be|'
        r'Use your org|All user edits|Tech Notes|List Emcee|'
        r'Use Script Below|Record (Virtual|Below)|For Video Recording)',
        re.IGNORECASE,
    )
    _BULLET    = re.compile(r'^[●•○■◆]')   # actual bullet characters only
    _STAGE_DIR = re.compile(r'^[\[\(]')    # [pause] or (AV notes)         # [pause for applause]
    _TS = re.compile(
        r'^(\d{1,2}:\d{2}(?:\s*[ap]m)?|\d{3,4}[ap]|\d{1,2}[ap])\s+(.+)$',
        re.IGNORECASE,
    )
    # Speaker cue: optional "M " prefix, Name:, optional trailing (stage direction)
    # Matches: "Kelly Russell:"  "M Susan Lieu:"  "Sheryl Feldman: (in-person only)"
    _SPEAKER = re.compile(
        r'^(?:(?:PM?|EM|AM|MC)\s+)?(?:Dr\.\s+|Mr\.\s+|Ms\.\s+|Sen\.\s+)?'
        r'([A-Z][a-zA-Z\-]+(?: [A-Z][a-zA-Z\-]+){0,3}):\s*(?:\([^)]*\))?\s*$'
    )
    # Common non-person words that look like speaker names but aren't
    _NOT_SPEAKER = re.compile(
        r'^(Platform|Thermometer|Livestream|Donation|Website|Hashtag|'
        r'Registration|Parking|Venue|Location|Time|Date|Note|Script|Cue|'
        r'Audio|Video|Music|Slide|Screen|Camera|AV\s|AV$)\b',
        re.IGNORECASE,
    )

    def _is_caps_line(text: str) -> bool:
        a = re.sub(r"[^A-Za-z ]", "", text).strip()
        w = [x for x in a.split() if len(x) >= 2]
        return bool(a and a.upper() == a and len(w) >= 2)

    _SINGLE_LETTER_CUE = re.compile(r'^[A-Z]\s+')  # "M Something", "S Something"

    def _summary(start_li: int, end_li: int) -> str:
        """Extract first meaningful sentence from the section's content lines."""
        for li in range(start_li + 1, min(end_li, start_li + 30)):
            text = lines[li].strip().rstrip('\r\n')
            if not text:
                continue
            if (_NOISE.match(text) or _BULLET.match(text) or _STAGE_DIR.match(text)
                    or _is_caps_line(text) or _SINGLE_LETTER_CUE.match(text)):
                continue
            # Stop at next speaker cue
            if _SPEAKER.match(text):
                break
            sentence = re.split(r'(?<=[.!?])\s', text)[0].strip()
            if len(sentence) >= 10:
                return (sentence[:110] + "…") if len(sentence) > 110 else sentence
        return ""

    def _add(sections, li, title, end_li):
        title = title.strip()
        if len(title) > 65:
            title = title[:62] + "…"
        sections.append({
            "title": title,
            "summary": _summary(li, end_li),
            "line_index": li,
            "word_index": line_to_word[li],
        })

    # ── Pass 1: timestamp-prefixed lines (event rundowns) ────────────────────
    # Only count a line as a scheduled item if the content after the timestamp
    # starts with an uppercase letter — filters mid-sentence time references like
    # "8:30AM until then, this is team..." which are NOT agenda items.
    timestamped_lis = []
    for li, text in enumerate(lines):
        stripped = text.strip()
        if not stripped or li not in line_to_word:
            continue
        m = _TS.match(stripped)
        if m:
            name = m.group(2).strip()
            if name and name[0].isupper():   # real agenda item starts uppercase
                timestamped_lis.append((li, m.group(1), name))

    if timestamped_lis:
        sections = []
        for idx, (li, ts, name) in enumerate(timestamped_lis):
            end_li = timestamped_lis[idx + 1][0] if idx + 1 < len(timestamped_lis) else len(lines)
            _add(sections, li, f"{ts}  {name}", end_li)
        return sections

    # ── Pass 2: speaker-cue + ALL CAPS (speaker scripts) ────────────────────
    candidate_lis = []
    prev_was_caps = False   # track consecutive ALL CAPS lines to collapse multi-line headers

    for li, text in enumerate(lines):
        stripped = text.strip().rstrip('\r\n')
        if not stripped or li not in line_to_word:
            continue
        if _NOISE.match(stripped) or _BULLET.match(stripped) or _STAGE_DIR.match(stripped):
            prev_was_caps = False
            continue

        is_speaker = bool(_SPEAKER.match(stripped) and not _NOT_SPEAKER.match(stripped))

        is_caps = False
        if not is_speaker:
            alpha_only = re.sub(r"[^A-Za-z ]", "", stripped).strip()
            alpha_words = [w for w in alpha_only.split() if len(w) >= 2]
            if (alpha_only and alpha_only.upper() == alpha_only
                    and len(alpha_words) >= 2 and len(stripped.split()) <= 8
                    and not stripped.endswith(')')):  # skip truncated AV-note fragments
                is_caps = True

        if is_speaker:
            # Always add speaker cues — strip leading cue prefix (M, PM, EM, AM, MC)
            display = re.sub(r'^(?:PM?|EM|AM|MC)\s+', '', stripped)
            candidate_lis.append((li, display))
            prev_was_caps = False
        elif is_caps:
            if not prev_was_caps:
                # First line of an ALL CAPS block — add it
                candidate_lis.append((li, stripped))
            # Stay in caps mode regardless (continuation lines get skipped next iteration)
            prev_was_caps = True
        else:
            prev_was_caps = False

    # Deduplicate same title only within a 6-line window (back-to-back cue repeats)
    deduped = []
    for li, title in candidate_lis:
        if deduped and deduped[-1][1] == title and li - deduped[-1][0] < 6:
            continue
        deduped.append((li, title))

    sections = []
    for idx, (li, title) in enumerate(deduped):
        end_li = deduped[idx + 1][0] if idx + 1 < len(deduped) else len(lines)
        _add(sections, li, title, end_li)

    return sections


def _result(lines: list, words: list, html) -> dict:
    return {
        "lines": lines,
        "words": words,
        "word_count": len(words),
        "line_count": len(lines),
        "html": html,
        "sections": _detect_sections(lines, words),
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
