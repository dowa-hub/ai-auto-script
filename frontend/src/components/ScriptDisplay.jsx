import { useEffect, useRef, useState } from 'react'

const LINE_HEIGHT = 36  // px — keep in sync with CSS

export default function ScriptDisplay({ script, currentLine, locked, onSeek }) {
  const containerRef = useRef(null)
  const lineRefs     = useRef([])
  const [manualScroll, setManualScroll] = useState(false)
  const scrollTimerRef = useRef(null)

  // Auto-scroll to current line unless operator has grabbed manual control
  useEffect(() => {
    if (manualScroll || !script || currentLine == null) return
    const el = lineRefs.current[currentLine]
    if (el && containerRef.current) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }, [currentLine, manualScroll, script])

  // Detect manual scroll — pause auto-scroll for 4 seconds then resume
  function onScroll() {
    setManualScroll(true)
    clearTimeout(scrollTimerRef.current)
    scrollTimerRef.current = setTimeout(() => setManualScroll(false), 4000)
  }

  if (!script || !script.lines) {
    return (
      <div style={{
        flex: 1,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        color: 'var(--text-dim)',
        fontSize: 15,
      }}>
        No script loaded
      </div>
    )
  }

  return (
    <div
      ref={containerRef}
      onScroll={onScroll}
      style={{
        flex: 1,
        overflowY: 'auto',
        padding: '32px 48px',
        scrollBehavior: 'smooth',
      }}
    >
      {/* Spacer so first line can center */}
      <div style={{ height: '40vh' }} />

      {script.lines.map((line, idx) => {
        const isCurrent  = idx === currentLine
        const isPast     = idx < currentLine
        const isNear     = Math.abs(idx - currentLine) <= 2

        return (
          <div
            key={idx}
            ref={el => (lineRefs.current[idx] = el)}
            onClick={() => {
              // Find the first word index for this line
              const wordIdx = script.words.findIndex(w => w.line_index === idx)
              if (wordIdx >= 0) onSeek(wordIdx)
            }}
            style={{
              lineHeight: `${LINE_HEIGHT}px`,
              minHeight: LINE_HEIGHT,
              padding: '4px 16px',
              marginBottom: 4,
              borderRadius: 6,
              cursor: 'pointer',
              fontFamily: line.includes('\t') ? "'JetBrains Mono', monospace" : "'Inter', sans-serif",
              fontSize: isCurrent ? 20 : isNear ? 17 : 15,
              fontWeight: isCurrent ? 600 : 400,
              whiteSpace: 'pre-wrap',
              color: isCurrent
                ? '#fff'
                : isPast
                ? 'var(--text-muted)'
                : isNear
                ? 'var(--text-dim)'
                : 'var(--text-dim)',
              background: isCurrent
                ? locked
                  ? 'rgba(33,150,243,0.2)'   // blue tint when locked
                  : 'var(--amber-mid)'
                : 'transparent',
              borderLeft: isCurrent
                ? `3px solid ${locked ? 'var(--blue)' : 'var(--amber)'}`
                : '3px solid transparent',
              transition: 'all 0.2s ease',
            }}
          >
            {line}
          </div>
        )
      })}

      {/* Spacer so last line can center */}
      <div style={{ height: '40vh' }} />
    </div>
  )
}
