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

export default function DocumentViewer({ fileInfo, currentLine, locked }) {
  const scrollRef   = useRef(null)   // outer scrollable div
  const innerRef    = useRef(null)   // inner div — PDF pages rendered here
  const highlightRef = useRef(null)  // amber bar (PDF mode, absolutely positioned)
  const pdfLinesRef = useRef([])     // [{yTop, yBottom}] in innerRef coords, per script line

  const [mode, setMode]           = useState(null)  // 'pdf' | 'html' | 'image' | null
  const [htmlContent, setHtml]    = useState('')
  const [loading, setLoading]     = useState(false)
  const [error, setError]         = useState('')

  // ── Load document whenever a new file is uploaded ─────────────────────────
  useEffect(() => {
    if (!fileInfo?.name) return
    load(fileInfo)
  }, [fileInfo?.name])

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

  // ── Respond to tracker position updates ───────────────────────────────────
  useEffect(() => {
    if (currentLine == null) return
    if (mode === 'pdf')  highlightPdf(currentLine)
    if (mode === 'html') highlightHtml(currentLine)
  }, [currentLine, mode])

  // ── PDF: canvas rendering ─────────────────────────────────────────────────
  async function renderPdf() {
    const lib  = await getPdfjs()
    const buf  = await fetch('/api/document/raw').then(r => r.arrayBuffer())
    const pdf  = await lib.getDocument({ data: buf }).promise
    const inner = innerRef.current
    if (!inner) return

    inner.innerHTML = ''
    const GAP = 20
    let topOffset = 0
    const allLines = []

    for (let p = 1; p <= pdf.numPages; p++) {
      const page     = await pdf.getPage(p)
      const rawVp    = page.getViewport({ scale: 1 })
      const maxW     = (scrollRef.current?.clientWidth || 900) - 48
      const scale    = Math.min(maxW / rawVp.width, 2.2)
      const viewport = page.getViewport({ scale })

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

      // Build line map from text layer
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
          buckets.set(key, { yTop, yBot })
        } else {
          const b = buckets.get(key)
          b.yTop = Math.min(b.yTop, yTop)
          b.yBot = Math.max(b.yBot, yBot)
        }
      }

      // Sort top → bottom, push to global line list with page offset
      ;[...buckets.entries()]
        .sort((a, b) => a[0] - b[0])
        .forEach(([, { yTop, yBot }]) => {
          allLines.push({ yTop: topOffset + yTop, yBot: topOffset + yBot })
        })

      topOffset += viewport.height + GAP
    }

    pdfLinesRef.current = allLines

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

  function highlightPdf(lineIdx) {
    const lines = pdfLinesRef.current
    const bar   = highlightRef.current
    if (!bar || !lines.length) return

    // Clamp to available lines
    const entry = lines[Math.min(lineIdx, lines.length - 1)]
    if (!entry) return

    bar.style.opacity = '1'
    bar.style.top     = entry.yTop + 'px'
    bar.style.height  = (entry.yBot - entry.yTop + 6) + 'px'

    // Scroll to center this line
    const sc = scrollRef.current
    if (sc) {
      const target = entry.yTop - sc.clientHeight / 2 + (entry.yBot - entry.yTop) / 2
      sc.scrollTo({ top: Math.max(0, target), behavior: 'smooth' })
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
    el.scrollIntoView({ behavior: 'smooth', block: 'center' })
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
      style={{ flex: 1, overflowY: 'auto', background: '#222', padding: '24px 0' }}
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
