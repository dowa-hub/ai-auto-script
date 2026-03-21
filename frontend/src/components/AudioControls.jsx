export default function AudioControls({
  devices, deviceId, setDeviceId,
  isCapturing, inputRate, level,
  onStart, onStop,
  connected, modelReady, sttStatus,
}) {
  const canStart = connected && modelReady && !isCapturing
  const bars     = 16
  const levelPct = Math.min(1, level * 6)  // scale RMS to visual range

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: 12,
      padding: '10px 20px',
      background: 'var(--surface)',
      borderTop: '1px solid var(--border)',
      flexShrink: 0,
    }}>
      {/* Device selector */}
      <select
        value={deviceId}
        onChange={e => setDeviceId(e.target.value)}
        disabled={isCapturing}
        style={{ maxWidth: 220 }}
        title="Audio input device"
      >
        {devices.length === 0 && <option value="">No devices found</option>}
        {devices.map(d => (
          <option key={d.deviceId} value={d.deviceId}>
            {d.label || `Input ${d.deviceId.slice(0, 8)}`}
          </option>
        ))}
      </select>

      {/* Sample rate badge */}
      {inputRate && (
        <span style={{
          fontSize: 11,
          color: 'var(--amber)',
          fontFamily: "'JetBrains Mono', monospace",
          whiteSpace: 'nowrap',
        }}>
          {(inputRate / 1000).toFixed(0)}kHz → 16kHz
        </span>
      )}

      {/* Level meter */}
      <div style={{ display: 'flex', gap: 2, alignItems: 'flex-end', height: 20 }}>
        {Array.from({ length: bars }, (_, i) => {
          const threshold = i / bars
          const active    = isCapturing && levelPct > threshold
          const isHot     = i >= bars * 0.85
          return (
            <div
              key={i}
              style={{
                width: 3,
                height: 4 + i * 1.2,
                borderRadius: 1,
                background: active
                  ? isHot ? 'var(--red)' : i >= bars * 0.6 ? 'var(--yellow)' : 'var(--green)'
                  : 'var(--border)',
                transition: 'background 0.05s',
              }}
            />
          )
        })}
      </div>

      {/* Start / Stop */}
      <button
        onClick={isCapturing ? onStop : onStart}
        disabled={!connected || !modelReady}
        style={{
          background: isCapturing ? 'var(--red)' : 'var(--amber)',
          color: '#000',
          fontWeight: 600,
          padding: '7px 20px',
          fontSize: 13,
        }}
      >
        {isCapturing ? '⏹ Stop' : '⏺ Listen'}
      </button>

      {/* Status pills */}
      <div style={{ display: 'flex', gap: 6, marginLeft: 'auto', alignItems: 'center' }}>
        <StatusPill label="WS" ok={connected} />
        <SttPill sttStatus={sttStatus} modelReady={modelReady} />
      </div>
    </div>
  )
}

function StatusPill({ label, ok }) {
  return (
    <span style={{
      fontSize: 11,
      fontWeight: 500,
      padding: '2px 8px',
      borderRadius: 999,
      background: ok ? 'rgba(76,175,80,0.15)' : 'rgba(255,152,0,0.15)',
      color: ok ? 'var(--green)' : 'var(--yellow)',
      border: `1px solid ${ok ? 'rgba(76,175,80,0.3)' : 'rgba(255,152,0,0.3)'}`,
    }}>
      {label}
    </span>
  )
}

function SttPill({ sttStatus, modelReady }) {
  if (!modelReady && !sttStatus) {
    return <StatusPill label="Loading model…" ok={false} />
  }
  if (!sttStatus) {
    return <StatusPill label="STT ready" ok={true} />
  }

  if (sttStatus.engine === 'deepgram') {
    if (sttStatus.connected) {
      return (
        <span style={{
          fontSize: 11, fontWeight: 600, padding: '2px 10px', borderRadius: 999,
          background: 'rgba(33,150,243,0.18)', color: '#64b5f6',
          border: '1px solid rgba(33,150,243,0.4)',
          display: 'flex', alignItems: 'center', gap: 5,
        }}>
          <span style={{ width: 7, height: 7, borderRadius: '50%', background: '#4caf50', display: 'inline-block' }} />
          Deepgram {sttStatus.model}
        </span>
      )
    } else {
      return (
        <span style={{
          fontSize: 11, fontWeight: 600, padding: '2px 10px', borderRadius: 999,
          background: 'rgba(244,67,54,0.15)', color: 'var(--red)',
          border: '1px solid rgba(244,67,54,0.3)',
          display: 'flex', alignItems: 'center', gap: 5,
        }}
          title={sttStatus.error || 'Connection failed'}
        >
          <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--red)', display: 'inline-block' }} />
          Deepgram error
        </span>
      )
    }
  }

  // Offline Whisper
  return (
    <span style={{
      fontSize: 11, fontWeight: 500, padding: '2px 10px', borderRadius: 999,
      background: 'rgba(76,175,80,0.12)', color: 'var(--green)',
      border: '1px solid rgba(76,175,80,0.3)',
      display: 'flex', alignItems: 'center', gap: 5,
    }}>
      <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--green)', display: 'inline-block' }} />
      Whisper {sttStatus.model}
    </span>
  )
}
