import FileUpload from './FileUpload.jsx'

export default function Sidebar({ onScriptLoaded, sttMode, whisperModel, onSettingsChange }) {
  return (
    <div style={{
      width: 280,
      flexShrink: 0,
      borderRight: '1px solid var(--border)',
      background: 'var(--surface)',
      display: 'flex',
      flexDirection: 'column',
      padding: '16px',
      gap: 24,
      overflowY: 'auto',
    }}>
      <div>
        <Logo />
      </div>

      <FileUpload onScriptLoaded={onScriptLoaded} />

      <Section title="STT Engine">
        <Label>Mode</Label>
        <select
          value={sttMode}
          onChange={e => onSettingsChange({ stt_mode: e.target.value })}
          style={{ width: '100%' }}
        >
          <option value="offline">Offline (Whisper)</option>
          <option value="cloud">Cloud (Deepgram)</option>
        </select>

        {sttMode === 'offline' && (
          <>
            <Label style={{ marginTop: 10 }}>Whisper model</Label>
            <select
              value={whisperModel}
              onChange={e => onSettingsChange({ whisper_model: e.target.value })}
              style={{ width: '100%' }}
            >
              <option value="tiny.en">tiny.en — fastest (~39 MB)</option>
              <option value="base.en">base.en — balanced (~150 MB)</option>
              <option value="small.en">small.en — better (~500 MB)</option>
              <option value="medium.en">medium.en — best (~1.5 GB)</option>
            </select>
            <p style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 6 }}>
              Downloaded on first use. Cached locally.
            </p>
          </>
        )}

        {sttMode === 'cloud' && (
          <>
            <Label style={{ marginTop: 10 }}>Deepgram API key</Label>
            <input
              type="password"
              placeholder="dg_..."
              onBlur={e => onSettingsChange({ deepgram_key: e.target.value })}
            />
            <p style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 6 }}>
              ~$0.006/min · 12,000 free minutes/year
            </p>
          </>
        )}
      </Section>

      <Section title="Tips">
        <ul style={{ fontSize: 12, color: 'var(--text-dim)', paddingLeft: 16, lineHeight: 1.7 }}>
          <li>Click any line to jump there</li>
          <li>Auto-tracking resumes 4s after manual scroll</li>
          <li>Use "Resume auto" to re-lock immediately</li>
          <li>Amber highlight = AI tracking</li>
          <li>Blue highlight = manually locked</li>
        </ul>
      </Section>
    </div>
  )
}

function Logo() {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <div style={{
        width: 28, height: 28,
        background: 'var(--amber)',
        borderRadius: 6,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 15,
      }}>
        🎙
      </div>
      <div>
        <div style={{ fontWeight: 700, fontSize: 14 }}>AI Auto Script</div>
        <div style={{ fontSize: 10, color: 'var(--text-dim)' }}>Script follower</div>
      </div>
    </div>
  )
}

function Section({ title, children }) {
  return (
    <div>
      <div style={{
        fontSize: 10,
        fontWeight: 700,
        letterSpacing: '0.08em',
        textTransform: 'uppercase',
        color: 'var(--text-dim)',
        marginBottom: 10,
      }}>
        {title}
      </div>
      {children}
    </div>
  )
}

function Label({ children, style }) {
  return (
    <div style={{ fontSize: 12, color: 'var(--text-dim)', marginBottom: 4, ...style }}>
      {children}
    </div>
  )
}
