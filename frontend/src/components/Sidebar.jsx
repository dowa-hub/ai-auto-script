import { useState, useRef } from 'react'
import FileUpload from './FileUpload.jsx'

export default function Sidebar({ onScriptLoaded, onSettingsChange, sttStatus }) {
  const [dgKey, setDgKey]       = useState('')
  const [connecting, setConn]   = useState(false)
  const inputRef                = useRef(null)

  async function connectDeepgram() {
    if (!dgKey.trim()) return
    setConn(true)
    await onSettingsChange({ deepgram_key: dgKey.trim() })
    setDgKey('')        // clear from UI immediately — never lingers
    setConn(false)
  }

  const dgConnected = sttStatus?.engine === 'deepgram' && sttStatus?.connected
  const dgError     = sttStatus?.engine === 'deepgram' && !sttStatus?.connected

  return (
    <div style={{
      width: 280, flexShrink: 0,
      borderRight: '1px solid var(--border)',
      background: 'var(--surface)',
      display: 'flex', flexDirection: 'column',
      padding: '16px', gap: 24, overflowY: 'auto',
    }}>
      <Logo />

      <FileUpload onScriptLoaded={(data, name) => onScriptLoaded(data, name)} />

      <Section title="STT Engine">

        {/* ── Deepgram section ── */}
        <Label>Deepgram (cloud)</Label>

        {dgConnected ? (
          // Connected state
          <div style={{
            background: 'rgba(33,150,243,0.1)', border: '1px solid rgba(33,150,243,0.3)',
            borderRadius: 6, padding: '8px 12px', marginBottom: 8,
            display: 'flex', alignItems: 'center',
          }}>
            <span style={{ fontSize: 12, color: '#64b5f6', display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#4caf50', display: 'inline-block' }} />
              Deepgram Nova-3 live
            </span>
          </div>
        ) : (
          // Paste key + connect
          <>
            {dgError && (
              <div style={{ fontSize: 11, color: 'var(--red)', marginBottom: 6 }}>
                Connection failed — check your key
              </div>
            )}
            <div style={{ display: 'flex', gap: 6 }}>
              <input
                ref={inputRef}
                type="password"
                value={dgKey}
                onChange={e => setDgKey(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && connectDeepgram()}
                placeholder="Paste API key…"
                style={{ flex: 1, minWidth: 0 }}
              />
              <button
                onClick={connectDeepgram}
                disabled={!dgKey.trim() || connecting}
                style={{ background: 'var(--amber)', color: '#000', fontWeight: 600, padding: '5px 10px', flexShrink: 0 }}
              >
                {connecting ? '…' : 'Connect'}
              </button>
            </div>
            <p style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 5 }}>
              Not saved anywhere · ~$0.006/min · 12k free min/yr
            </p>
          </>
        )}

      </Section>

      <Section title="Tips">
        <ul style={{ fontSize: 12, color: 'var(--text-dim)', paddingLeft: 16, lineHeight: 1.8 }}>
          <li>Click any line to jump there manually</li>
          <li>Auto-tracking resumes after 4s</li>
          <li>Amber = AI tracking · Blue = manual lock</li>
          <li>API key is never stored to disk</li>
        </ul>
      </Section>
    </div>
  )
}

function Logo() {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <div style={{
        width: 28, height: 28, background: 'var(--amber)',
        borderRadius: 6, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 15,
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
        fontSize: 10, fontWeight: 700, letterSpacing: '0.08em',
        textTransform: 'uppercase', color: 'var(--text-dim)', marginBottom: 10,
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
