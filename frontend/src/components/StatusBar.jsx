function getDisplay(trackerStatus, locked) {
  if (locked) {
    return { color: '#2196f3', primary: 'MANUAL LOCK', secondary: 'Auto-tracking paused — click Resume to hand back' }
  }
  const { state, confidence, missCount } = trackerStatus
  const pct = Math.round((confidence ?? 0) * 100)

  switch (state) {
    case 'tracking':
      if (confidence >= 0.93) return { color: '#4caf50', primary: 'TRACKING — LOCKED ON',      secondary: 'Exact word match · following the speaker' }
      if (confidence >= 0.83) return { color: '#4caf50', primary: 'TRACKING — NEAR MATCH',     secondary: 'Minor speech variation · still following' }
      if (confidence >= 0.78) return { color: '#8bc34a', primary: 'TRACKING — RELOCATED',      secondary: 'Jumped to closest match · watching closely' }
      if (confidence >= 0.68) return { color: '#ffc107', primary: 'TRACKING — PARTIAL MATCH',  secondary: 'Partial word match · lower certainty' }
      return                         { color: '#ffc107', primary: 'TRACKING — TOPIC MATCH',    secondary: 'Matched by topic area · position approximate' }

    case 'holding':
      return { color: '#ff9800', primary: 'HOLDING — MATCH TOO WEAK',   secondary: `Saw a possible match at ${pct}% — not confident enough to move` }

    case 'searching':
      return { color: '#ff9800', primary: 'SEARCHING — SCANNING SCRIPT', secondary: `Off script for ${missCount} updates · scanning for re-entry point` }

    case 'waiting':
      return { color: '#999',    primary: 'WAITING — SPEAKER OFF SCRIPT', secondary: 'Listening · will lock on when script words are spoken' }

    case 'idle':
    default:
      return { color: '#555',    primary: 'READY',                        secondary: 'Load a script and start listening' }
  }
}

export default function StatusBar({
  transcript, confidence, locked, trackerStatus = {},
  currentLine, lineCount, onResume, onReset,
}) {
  const pct = Math.round((confidence ?? 0) * 100)
  const { color, primary, secondary } = getDisplay(trackerStatus, locked)

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: 16,
      padding: '0 20px',
      background: 'var(--surface2)',
      borderBottom: '1px solid var(--border)',
      flexShrink: 0,
      height: 88,
    }}>

      {/* Left — line counter */}
      <div style={{ minWidth: 80, flexShrink: 0 }}>
        <div style={{ fontSize: 13, color: 'var(--text-dim)', whiteSpace: 'nowrap' }}>
          Line {currentLine != null ? currentLine + 1 : '—'}
          {lineCount ? ` / ${lineCount}` : ''}
        </div>
      </div>

      {/* Center — large confidence + status */}
      <div style={{
        flex: 1,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 16,
      }}>
        {/* Big confidence number */}
        <div style={{
          fontSize: 42,
          fontWeight: 800,
          fontFamily: "'JetBrains Mono', monospace",
          color,
          transition: 'color 0.4s',
          minWidth: 90,
          textAlign: 'right',
          lineHeight: 1,
          flexShrink: 0,
        }}>
          {pct}%
        </div>

        {/* Status lines */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 3, flex: 1, minWidth: 0, overflow: 'hidden' }}>
          <div style={{
            fontSize: 15,
            fontWeight: 700,
            letterSpacing: '0.06em',
            color,
            transition: 'color 0.4s',
          }}>
            {primary}
          </div>
          <div style={{
            fontSize: 13,
            color: 'var(--text-dim)',
            fontStyle: 'italic',
          }}>
            {secondary}
          </div>
          {/* Live transcript */}
          {transcript && (
            <div style={{
              fontSize: 12,
              color: '#777',
              overflow: 'hidden',
              whiteSpace: 'nowrap',
              textOverflow: 'ellipsis',
              maxWidth: 500,
            }}>
              "{transcript}"
            </div>
          )}
        </div>
      </div>

      {/* Right — controls */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
        {locked && (
          <button
            onClick={onResume}
            style={{ background: 'var(--blue)', color: '#fff', padding: '4px 12px', fontSize: 12, fontWeight: 600 }}
          >
            Resume auto
          </button>
        )}
        <button
          onClick={onReset}
          style={{
            background: 'var(--surface)', color: 'var(--text-dim)',
            border: '1px solid var(--border)', padding: '4px 12px', fontSize: 12,
          }}
        >
          Reset
        </button>
      </div>

    </div>
  )
}
