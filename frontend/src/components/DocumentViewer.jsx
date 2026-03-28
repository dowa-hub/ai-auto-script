/**
 * DocumentViewer — renders the original document untouched and overlays
 * the AI position tracking on top of it.
 *
 * PDF  → PDF.js canvas rendering + amber highlight bar at current line
 * DOCX/XLSX/TXT → backend-generated HTML (tables/formatting preserved) + CSS highlight
 * Image → plain <img>, no per-line highlight
 */
import { useEffect, useRef, useState } from 'react'

// ── PDF.js lazy loader ────────────────────────────────────────────────────────
let _pdfjs = null
async function getPdfjs() {
  if (_pdfjs) return _pdfjs
  const mod = await import('pdfjs-dist')
  mod.GlobalWorkerOptions.workerSrc = new URL(
    'pdfjs-dist/build/pdf.worker.min.mjs',
    import.meta.url,
  ).href
  _pdfjs = mod
  return mod
}

const IMAGE_EXTS = new Set(['jpg', 'jpeg', 'png', 'tiff', 'tif', 'bmp', 'webp'])
const HTML_EXTS  = new Set(['docx', 'doc', 'xlsx', 'xls', 'txt', 'text'])

// Slow, steady scroll animation (easeInOutCubic) so the reader can follow along
let _scrollRaf = null
function smoothScrollTo(element, targetY, duration = 800) {
  if (_scrollRaf) cancelAnimationFrame(_scrollRaf)
  const startY = element.scrollTop
  const diff = targetY - startY
  if (Math.abs(diff) < 5) return  // already there
  const startTime = performance.now()

  function step(now) {
    const elapsed = now - startTime
    const t = Math.min(elapsed / duration, 1)
    // easeInOutCubic — smooth acceleration and deceleration
    const ease = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2
    element.scrollTop = startY + diff * ease
    if (t < 1) _scrollRaf = requestAnimationFrame(step)
  }
  _scrollRaf = requestAnimationFrame(step)
}

export default function DocumentViewer({ fileInfo, currentLine, locked, scriptData, onSeek, sectionTarget }) {
  const scrollRef      = useRef(null)   // outer scrollable div
  const innerRef       = useRef(null)   // inner div — PDF pages rendered here
  const highlightRef   = useRef(null)   // amber bar (PDF mode, absolutely positioned)
  const pdfLinesRef    = useRef([])     // [{yTop, yBottom}] in innerRef coords, per script line
  const pdfTextRowsRef = useRef([])     // [{text, yTop, yBot, page}] — all text rows with positions
  const lineToPdfRow   = useRef({})     // backend line_index → PDF row index (built once at load)
  const pageYOffsets   = useRef([])     // [topY] per 0-based page index
  const pageHeights    = useRef([])     // [height] per 0-based page index (rendered pixels)
  const rowToPage      = useRef([])     // PDF row index → 0-based page number
  const pdfDocRef      = useRef(null)   // PDF.js document — destroyed on re-upload
  const sectionSeekTs  = useRef(0)      // timestamp of last section seek (cooldown for line scroll)

  const [mode, setMode]           = useState(null)  // 'pdf' | 'html' | 'image' | null
  const [htmlContent, setHtml]    = useState('')
  const [loading, setLoading]     = useState(false)
  const [error, setError]         = useState('')
  const [loadCount, setLoadCount] = useState(0)    // increments on every upload

  // ── Load document whenever a new file is uploaded ─────────────────────────
  useEffect(() => {
    if (!fileInfo?.name) return
    setLoadCount(c => c + 1)
    load(fileInfo)
    return () => {
      if (pdfDocRef.current) {
        pdfDocRef.current.destroy()
        pdfDocRef.current = null
      }
    }
  }, [fileInfo])

  async function load({ name }) {
    const ext = name.split('.').pop().toLowerCase()
    setLoading(true)
    setError('')
    setMode(null)
    setHtml('')
    if (innerRef.current) innerRef.current.innerHTML = ''

    try {
      if (ext === 'pdf') {
        await renderPdf()
        setMode('pdf')
      } else if (IMAGE_EXTS.has(ext)) {
        setMode('image')
      } else if (HTML_EXTS.has(ext)) {
        const res = await fetch('/api/document/html')
        if (!res.ok) throw new Error(await res.text())
        setHtml(await res.text())
        setMode('html')
      } else {
        throw new Error(`Unsupported file type: .${ext}`)
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  // ── Rebuild PDF line mapping when scriptData arrives ──────────────────────
  useEffect(() => {
    if (mode === 'pdf' && scriptData?.lines && pdfTextRowsRef.current.length) {
      buildLineMapping(pdfTextRowsRef.current)
    }
  }, [scriptData, mode])

  // ── Respond to tracker position updates ───────────────────────────────────
  useEffect(() => {
    if (currentLine == null) return
    // Skip entirely if a section seek just happened — sectionTarget owns positioning
    if (Date.now() - sectionSeekTs.current < 1500) return
    if (mode === 'pdf')  highlightPdfByText(currentLine)
    if (mode === 'html') highlightHtml(currentLine)
  }, [currentLine, mode])

  // ── Direct PDF navigation from section click (bypasses word→line→row chain) ─
  useEffect(() => {
    if (!sectionTarget || mode !== 'pdf') return
    const { page, pageY } = sectionTarget
    const offset = pageYOffsets.current[page]
    const height = pageHeights.current[page]
    if (offset == null || !height) return

    sectionSeekTs.current = Date.now()
    const targetY = offset + pageY * height

    // Position highlight bar directly at the section's PDF coordinates
    const bar = highlightRef.current
    if (bar) {
      bar.style.opacity = '1'
      bar.style.top     = (targetY - 4) + 'px'
      bar.style.height  = '26px'
    }

    // Scroll directly to the timestamp's position in the PDF
    const sc = scrollRef.current
    if (sc) smoothScrollTo(sc, Math.max(0, targetY - sc.clientHeight * 0.2), 600)
  }, [sectionTarget, mode])

  // ── PDF: canvas rendering ─────────────────────────────────────────────────
  async function renderPdf() {
    const lib  = await getPdfjs()
    const buf  = await fetch('/api/document/raw').then(r => r.arrayBuffer())
    if (pdfDocRef.current) pdfDocRef.current.destroy()
    const pdf  = await lib.getDocument({ data: buf }).promise
    pdfDocRef.current = pdf
    const inner = innerRef.current
    if (!inner) return

    inner.innerHTML = ''
    const GAP = 20
    let topOffset = 0
    const allLines = []
    const allTextRows = []
    const _pageYOffsets = []
    const _pageHeights  = []
    const _rowToPage    = []

    for (let p = 1; p <= pdf.numPages; p++) {
      const pageIdx  = p - 1
      const page     = await pdf.getPage(p)
      const rawVp    = page.getViewport({ scale: 1 })
      const maxW     = (scrollRef.current?.clientWidth || 900) - 48
      const scale    = Math.min(maxW / rawVp.width, 2.2)
      const viewport = page.getViewport({ scale })
      _pageYOffsets.push(topOffset)
      _pageHeights.push(viewport.height)

      // Page wrapper (white card)
      const wrap = document.createElement('div')
      wrap.style.cssText = [
        'position:relative',
        'display:block',
        `width:${viewport.width}px`,
        `height:${viewport.height}px`,
        `margin:0 auto ${GAP}px`,
        'background:#fff',
        'box-shadow:0 4px 24px rgba(0,0,0,0.55)',
      ].join(';')

      const canvas = document.createElement('canvas')
      canvas.width  = viewport.width
      canvas.height = viewport.height
      canvas.style.display = 'block'
      await page.render({ canvasContext: canvas.getContext('2d'), viewport }).promise
      wrap.appendChild(canvas)
      inner.appendChild(wrap)

      // Build line map from text layer — store text + positions
      const tc = await page.getTextContent()
      const buckets = new Map()

      for (const item of tc.items) {
        if (!item.str?.trim()) continue
        const [, vy] = viewport.convertToViewportPoint(item.transform[4], item.transform[5])
        const fontH  = Math.abs(item.transform[3]) * scale || 12
        const yTop   = vy - fontH
        const yBot   = vy + 2
        const key    = Math.round(vy / 10) * 10  // ~10px buckets = one visual line
        if (!buckets.has(key)) {
          buckets.set(key, { yTop, yBot, texts: [item.str.trim()] })
        } else {
          const b = buckets.get(key)
          b.yTop = Math.min(b.yTop, yTop)
          b.yBot = Math.max(b.yBot, yBot)
          b.texts.push(item.str.trim())
        }
      }

      // Sort top → bottom, push to global line list with page offset
      ;[...buckets.entries()]
        .sort((a, b) => a[0] - b[0])
        .forEach(([, { yTop, yBot, texts }]) => {
          const row = {
            yTop: topOffset + yTop,
            yBot: topOffset + yBot,
            text: texts.join(' ').toLowerCase().replace(/[^\w\s']/g, ''),
            page: pageIdx,
          }
          _rowToPage.push(pageIdx)
          allLines.push(row)
          allTextRows.push(row)
        })

      topOffset += viewport.height + GAP
    }

    pdfLinesRef.current    = allLines
    pdfTextRowsRef.current = allTextRows
    pageYOffsets.current   = _pageYOffsets
    pageHeights.current    = _pageHeights
    rowToPage.current      = _rowToPage

    // Build a mapping from backend line_index → PDF row index
    // by matching words sequentially between both sides
    buildLineMapping(allTextRows)

    // Amber highlight bar — absolutely positioned inside inner
    const bar = document.createElement('div')
    bar.style.cssText = [
      'position:absolute',
      'left:0', 'right:0',
      'background:rgba(245,166,35,0.22)',
      'border-left:4px solid #f5a623',
      'pointer-events:none',
      'transition:top 0.25s ease, height 0.25s ease',
      'opacity:0',
      'z-index:10',
    ].join(';')
    inner.appendChild(bar)
    highlightRef.current = bar
  }

  function buildLineMapping(pdfRows) {
    // Build a one-time mapping: backend line_index → PDF row index.
    //
    // Key insight: multi-column PDFs have extra words (Time, Cues columns) between
    // script words, so a global sequential search drifts badly. Instead we partition
    // by page — the backend tells us which page each line came from (scriptData.line_pages),
    // and we only match against PDF rows from that same page. This makes the search
    // window irrelevant and eliminates cross-page false matches entirely.
    if (!scriptData?.lines || !pdfRows.length) return

    const linePagesMap = scriptData.line_pages || {}  // line_index → page_num (0-based)
    const hasPageInfo  = Object.keys(linePagesMap).length > 0

    // Group PDF rows by page
    const rowsByPage = {}  // page → [{rowIdx, words:[]}]
    for (let r = 0; r < pdfRows.length; r++) {
      const pg = pdfRows[r].page ?? rowToPage.current[r] ?? 0
      if (!rowsByPage[pg]) rowsByPage[pg] = []
      const words = pdfRows[r].text.split(/\s+/).filter(w => w.length > 1)
      rowsByPage[pg].push({ rowIdx: r, words })
    }

    // Build flat pdfWords per page for sequential scanning within page
    const pdfWordsByPage = {}
    for (const [pg, rows] of Object.entries(rowsByPage)) {
      pdfWordsByPage[pg] = []
      for (const { rowIdx, words } of rows) {
        for (const w of words) pdfWordsByPage[pg].push({ rowIdx, word: w })
      }
    }

    const mapping = {}
    // Track per-page scan pointer so sequential within-page matching stays in order
    const pagePointers = {}

    for (let l = 0; l < scriptData.lines.length; l++) {
      const cleaned = scriptData.lines[l].toLowerCase().replace(/[^\w\s]/g, '')
      const lineWords = cleaned.split(/\s+/).filter(w => w.length > 1)
      if (!lineWords.length) continue

      const pg = hasPageInfo ? (linePagesMap[l] ?? 0) : 0
      const pageWords = pdfWordsByPage[pg] || []
      if (!pagePointers[pg]) pagePointers[pg] = 0

      let pi = pagePointers[pg]
      const target = lineWords[0]

      // Timestamps (e.g. "530pm", "615am") are section anchors — search the full
      // page for them so cue-column lines can't advance the pointer past them.
      // Regular lines use the sequential forward window (faster, stays in order).
      const isTimestamp = /^\d{3,6}[a-z]{0,2}$/.test(target)
      const searchStart = isTimestamp ? 0 : pi
      const searchEnd   = isTimestamp ? pageWords.length : Math.min(pi + 80, pageWords.length)

      for (let j = searchStart; j < searchEnd; j++) {
        const pw = pageWords[j]
        if (pw.word === target ||
            (target.length >= 4 && pw.word.length >= 4 && target.slice(0, 4) === pw.word.slice(0, 4))) {
          if (!(l in mapping)) mapping[l] = pw.rowIdx
          // Only advance pointer forward, never back (timestamps may find rows behind current pos)
          if (j + 1 > pi) pagePointers[pg] = j + 1
          break
        }
      }
    }

    // ── Pass 2: re-map section anchor lines using direct title-text search ───
    // The sequential pointer can drift when cue/lighting column lines advance
    // it past a section header. Re-map every section's line_index by searching
    // the section TITLE text directly in the PDF rows (no pointer dependency).
    // Uses bigram matching: first two title words must appear in the same row.
    if (scriptData.sections?.length) {
      for (const sec of scriptData.sections) {
        const li  = sec.line_index
        const pg  = hasPageInfo ? (linePagesMap[li] ?? 0) : 0
        const titleClean = sec.title.toLowerCase().replace(/[^\w\s]/g, '').trim()
        const tWords = titleClean.split(/\s+/).filter(w => w.length > 1)
        if (!tWords.length) continue
        const t0 = tWords[0]   // usually the timestamp
        const t1 = tWords[1]   // usually the speaker name
        const pageRows = rowsByPage[pg] || []
        for (const { rowIdx, words } of pageRows) {
          const i0 = words.findIndex(w =>
            w === t0 || (t0.length >= 4 && w.length >= 4 && t0.slice(0, 4) === w.slice(0, 4))
          )
          if (i0 === -1) continue
          if (t1) {
            // Second word must appear within 4 positions of the first
            const nearby = words.slice(i0, i0 + 5)
            const ok = nearby.some(w =>
              w === t1 || (t1.length >= 4 && w.length >= 4 && t1.slice(0, 4) === w.slice(0, 4))
            )
            if (!ok) continue
          }
          mapping[li] = rowIdx   // override sequential result
          break
        }
      }
    }

    lineToPdfRow.current = mapping
    console.log(`[PDF] Built line mapping: ${Object.keys(mapping).length}/${scriptData.lines.length} lines mapped, ${scriptData.sections?.length ?? 0} sections re-anchored`)
  }

  function highlightPdfByText(lineIdx) {
    const rows = pdfTextRowsRef.current
    const bar  = highlightRef.current
    if (!bar || !rows.length) return

    // Use the pre-built mapping to find the correct PDF row
    const mapping = lineToPdfRow.current
    let rowIdx = mapping[lineIdx]

    // If exact line not mapped, find nearest mapped line
    if (rowIdx == null) {
      let closest = null
      let closestDist = Infinity
      for (const [lineStr, rIdx] of Object.entries(mapping)) {
        const dist = Math.abs(parseInt(lineStr) - lineIdx)
        if (dist < closestDist) {
          closestDist = dist
          closest = rIdx
        }
      }
      if (closest != null) rowIdx = closest
    }

    // Page-level fallback: if no row matched, scroll to the page the backend says this line is on
    if (rowIdx == null) {
      const pg = scriptData?.line_pages?.[lineIdx]
      if (pg != null && pageYOffsets.current[pg] != null) {
        const sc = scrollRef.current
        if (sc) smoothScrollTo(sc, Math.max(0, pageYOffsets.current[pg] - sc.clientHeight * 0.1), 800)
        if (bar) bar.style.opacity = '0'
        return
      }
      rowIdx = Math.min(lineIdx, rows.length - 1)
    }

    const entry = rows[rowIdx]
    if (!entry) return

    bar.style.opacity = '1'
    bar.style.top     = entry.yTop + 'px'
    bar.style.height  = (entry.yBot - entry.yTop + 6) + 'px'

    // Scroll so highlight sits ~20% from top — leaves plenty of script visible below
    // Uses slow animated scroll so the reader's eyes can follow along
    const sc = scrollRef.current
    if (sc) {
      const target = Math.max(0, entry.yTop - sc.clientHeight * 0.2)
      smoothScrollTo(sc, target, 800)  // 800ms duration
    }
  }

  // ── HTML mode (DOCX / XLSX / TXT) ─────────────────────────────────────────
  function highlightHtml(lineIdx) {
    const sc = scrollRef.current
    if (!sc) return
    sc.querySelectorAll('.ais-current').forEach(el => el.classList.remove('ais-current'))
    const el = sc.querySelector(`[data-line="${lineIdx}"]`)
    if (!el) return
    el.classList.add('ais-current')
    el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  // ── Click-to-seek ──────────────────────────────────────────────────────────
  function handleDocClick(e) {
    if (!onSeek || !scriptData?.words) return

    if (mode === 'html') {
      const lineEl = e.target.closest('[data-line]')
      if (!lineEl) return
      const lineIdx = parseInt(lineEl.getAttribute('data-line'), 10)
      if (isNaN(lineIdx)) return
      const wordIndex = scriptData.words.findIndex(w => w.line_index === lineIdx)
      if (wordIndex >= 0) {
        // Brief blue flash so the operator knows the click registered
        lineEl.style.outline = '2px solid #2196f3'
        lineEl.style.background = 'rgba(33,150,243,0.15)'
        setTimeout(() => {
          lineEl.style.outline = ''
          lineEl.style.background = ''
        }, 600)
        onSeek(wordIndex)
      }
    }

    if (mode === 'pdf') {
      const inner = innerRef.current
      if (!inner) return
      const rect = inner.getBoundingClientRect()
      const clickY = e.clientY - rect.top + scrollRef.current.scrollTop
      const rows = pdfTextRowsRef.current
      if (!rows.length) return
      let bestRow = 0, bestDist = Infinity
      for (let i = 0; i < rows.length; i++) {
        const mid = (rows[i].yTop + rows[i].yBot) / 2
        const dist = Math.abs(mid - clickY)
        if (dist < bestDist) { bestDist = dist; bestRow = i }
      }
      let targetLine = null, closestDist = Infinity
      for (const [lineStr, rowIdx] of Object.entries(lineToPdfRow.current)) {
        const dist = Math.abs(rowIdx - bestRow)
        if (dist < closestDist) { closestDist = dist; targetLine = parseInt(lineStr, 10) }
      }
      if (targetLine == null) return
      const wordIndex = scriptData.words.findIndex(w => w.line_index === targetLine)
      if (wordIndex >= 0) onSeek(wordIndex)
    }
  }

  // ── Render ─────────────────────────────────────────────────────────────────
  if (!fileInfo) {
    return (
      <div style={{
        flex: 1, display: 'flex', alignItems: 'center',
        justifyContent: 'center', color: 'var(--text-dim)', fontSize: 15,
      }}>
        No document loaded
      </div>
    )
  }

  return (
    <div
      ref={scrollRef}
      onClick={handleDocClick}
      style={{ flex: 1, overflowY: 'auto', background: '#222', padding: '24px 0', cursor: onSeek ? 'pointer' : 'default' }}
    >
      {loading && (
        <div style={{ textAlign: 'center', paddingTop: 80, color: 'var(--text-dim)' }}>
          Rendering document…
        </div>
      )}
      {error && (
        <div style={{ textAlign: 'center', paddingTop: 80, color: 'var(--red)' }}>{error}</div>
      )}

      {/* PDF: imperative canvas pages go here */}
      <div
        ref={innerRef}
        style={{ position: 'relative', display: mode === 'pdf' ? 'block' : 'none' }}
      />

      {/* HTML (DOCX / XLSX / TXT) */}
      {mode === 'html' && (
        <>
          <style>{HTML_STYLES}</style>
          <div
            className="doc-body"
            dangerouslySetInnerHTML={{ __html: htmlContent }}
          />
        </>
      )}

      {/* Image */}
      {mode === 'image' && (
        <img
          src="/api/document/raw"
          alt="Script"
          style={{
            maxWidth: '100%', display: 'block',
            margin: '0 auto', boxShadow: '0 4px 24px rgba(0,0,0,0.55)',
          }}
        />
      )}
    </div>
  )
}

// ── Styles injected alongside the HTML document ───────────────────────────────
const HTML_STYLES = `
  .doc-body {
    background: #fff;
    color: #111;
    padding: 56px 72px;
    max-width: 960px;
    margin: 0 auto;
    min-height: 80vh;
    font-family: Georgia, 'Times New Roman', serif;
    font-size: 14px;
    line-height: 1.75;
    box-shadow: 0 4px 24px rgba(0,0,0,0.55);
  }
  .doc-body p   { margin: 0 0 6px; }
  .doc-body h1, .doc-body h2, .doc-body h3 { margin: 18px 0 8px; }
  .doc-body table {
    border-collapse: collapse;
    width: 100%;
    margin: 12px 0;
    font-family: 'Courier New', monospace;
    font-size: 13px;
  }
  .doc-body td, .doc-body th {
    border: 1px solid #bbb;
    padding: 5px 10px;
    vertical-align: top;
  }
  .doc-body tr:nth-child(even) { background: #f9f9f9; }
  .sheet-name { font-family: sans-serif; font-size: 13px; color: #555; margin: 16px 0 4px; }

  /* Tracking highlight */
  .ais-current {
    background: rgba(245, 166, 35, 0.25) !important;
    outline: 2px solid rgba(245, 166, 35, 0.6);
    outline-offset: 1px;
    border-radius: 2px;
  }
`
