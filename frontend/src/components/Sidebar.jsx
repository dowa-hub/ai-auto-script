import { useState, useRef } from 'react'
import FileUpload from './FileUpload.jsx'

export default function Sidebar({ onScriptLoaded, sttMode, whisperModel, onSettingsChange, sttStatus }) {
  const [dgKey, setDgKey]       = useState('')
  const [connecting, setConn]   = useState(false)
  const inputRef                = useRef(null)

  async function connectDeepgram() {
    if (!dgKey.trim()) return
    setConn(true)
    await onSettingsChange({ stt_mode: 'cloud', deepgram_key: dgKey.trim() })
    setDgKey('')        // clear from UI immediately — never lingers
    setConn(false)
  }

  async function disconnectDeepgram() {
    await onSettingsChange({ stt_mode: 'offline' })
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
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          }}>
            <span style={{ fontSize: 12, color: '#64b5f6', display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#4caf50', display: 'inline-block' }} />
              Deepgram Nova-3 live
            </span>
            <button
              onClick={disconnectDeepgram}
              style={{ background: 'transparent', color: 'var(--text-dim)', fontSize: 11, padding: '2px 6px', border: '1px solid var(--border)' }}
            >
              Disconnect
            </button>
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

        {/* ── Whisper section ── */}
        <Label style={{ marginTop: 14 }}>Whisper (offline)</Label>
        <select
          value={whisperModel}
          onChange={e => onSettingsChange({ whisper_model: e.target.value, stt_mode: 'offline' })}
          disabled={dgConnected}
          style={{ width: '100%' }}
          title={dgConnected ? 'Disconnect Deepgram to use Whisper' : ''}
        >
          <option value="tiny.en">tiny.en — fastest</option>
          <option value="base.en">base.en — balanced</option>
          <option value="small.en">small.en — better</option>
          <option value="medium.en">medium.en — best</option>
        </select>
        {dgConnected && (
          <p style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 4 }}>
            Disconnect Deepgram to switch to offline
          </p>
        )}
      </Section>

      <Section title="Tips">
        <ul style={{ fontSize: 12, color: 'var(--text-dim)', paddingLeft: 16, lineHeight: 1.8 }}>
          <li>Click any line to jump there manually</li>
          <li>Auto-tracking resumes after 4s</li>
          <li>Amber = AI tracking · Blue = manual lock</li>
          <li>Deepgram key is never stored to disk</li>
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
