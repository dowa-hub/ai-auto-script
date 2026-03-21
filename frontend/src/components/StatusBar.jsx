export default function StatusBar({
  transcript, confidence, locked,
  currentLine, lineCount, onResume, onReset,
}) {
  const pct   = Math.round((confidence ?? 0) * 100)
  const color = pct >= 70 ? 'var(--green)' : pct >= 45 ? 'var(--yellow)' : 'var(--red)'

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: 16,
      padding: '6px 20px',
      background: 'var(--surface2)',
      borderBottom: '1px solid var(--border)',
      flexShrink: 0,
      minHeight: 38,
    }}>
      {/* Position counter */}
      <span style={{ fontSize: 12, color: 'var(--text-dim)', whiteSpace: 'nowrap' }}>
        Line {currentLine != null ? currentLine + 1 : '—'}
        {lineCount ? ` / ${lineCount}` : ''}
      </span>

      {/* Confidence bar */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <div style={{
          width: 80, height: 4, borderRadius: 2,
          background: 'var(--border)', overflow: 'hidden',
        }}>
          <div style={{
            height: '100%', width: `${pct}%`,
            background: color,
            transition: 'width 0.3s, background 0.3s',
          }} />
        </div>
        <span style={{ fontSize: 11, color, fontFamily: "'JetBrains Mono', monospace" }}>
          {pct}%
        </span>
      </div>

      {/* Live transcript snippet */}
      <span style={{
        flex: 1,
        fontSize: 12,
        color: 'var(--text-dim)',
        overflow: 'hidden',
        whiteSpace: 'nowrap',
        textOverflow: 'ellipsis',
        fontStyle: 'italic',
      }}>
        {transcript || ''}
      </span>

      {/* Locked indicator + resume button */}
      {locked && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 11, color: 'var(--blue)' }}>
            🔒 Manual
          </span>
          <button
            onClick={onResume}
            style={{ background: 'var(--blue)', color: '#fff', padding: '3px 10px', fontSize: 12 }}
          >
            Resume auto
          </button>
        </div>
      )}

      {/* Reset */}
      <button
        onClick={onReset}
        style={{ background: 'var(--surface)', color: 'var(--text-dim)',
                 border: '1px solid var(--border)', padding: '3px 10px', fontSize: 12 }}
      >
        Reset
      </button>
    </div>
  )
}
