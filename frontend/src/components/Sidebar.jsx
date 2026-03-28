import { useState, useRef } from 'react'

const ACCEPTED = '.pdf,.docx,.doc,.xlsx,.xls,.txt,.jpg,.jpeg,.png,.tiff,.tif'

export default function Sidebar({ onScriptLoaded, onSettingsChange, sttStatus, sections = [], onSectionSeek }) {
  const [dgKey,      setDgKey]    = useState('')
  const [connecting, setConn]     = useState(false)
  const [uploading,  setUploading] = useState(false)
  const [uploadErr,  setUploadErr] = useState('')
  const fileRef = useRef(null)

  const dgConnected = sttStatus?.engine === 'deepgram' && sttStatus?.connected
  const dgError     = sttStatus?.engine === 'deepgram' && !sttStatus?.connected && sttStatus?.error

  async function handleFile(file) {
    if (!file) return
    setUploading(true)
    setUploadErr('')
    try {
      const form = new FormData()
      form.append('file', file)
      const res  = await fetch('/api/upload-script', { method: 'POST', body: form })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Upload failed')
      const scriptRes  = await fetch('/api/script')
      const scriptData = await scriptRes.json()
      onScriptLoaded(scriptData, file.name)
    } catch (e) {
      setUploadErr(e.message)
    } finally {
      setUploading(false)
    }
  }

  async function connectDeepgram() {
    if (!dgKey.trim()) return
    setConn(true)
    try {
      await onSettingsChange({ deepgram_key: dgKey.trim() })
      setDgKey('')
    } catch {
      setUploadErr('Connection failed — check your key')
    } finally {
      setConn(false)
    }
  }

  return (
    <div
      style={{
        width: 280, flexShrink: 0,
        borderRight: '1px solid var(--border)',
        background: 'var(--surface)',
        display: 'flex', flexDirection: 'column',
        overflow: 'hidden',
      }}
      onDragOver={e => e.preventDefault()}
      onDrop={e => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) handleFile(f) }}
    >

      {/* ── Top bar: logo + import button ── */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '13px 14px 11px',
        borderBottom: '1px solid var(--border)',
        flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{
            width: 26, height: 26, background: 'var(--amber)', borderRadius: 5,
            display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 13, flexShrink: 0,
          }}>🎙</div>
          <span style={{ fontWeight: 700, fontSize: 13 }}>AI Auto Script</span>
        </div>

        <button
          onClick={() => fileRef.current?.click()}
          disabled={uploading}
          style={{
            background: 'var(--amber)', color: '#000',
            fontWeight: 700, fontSize: 12,
            padding: '5px 12px', borderRadius: 4,
            cursor: uploading ? 'wait' : 'pointer',
            flexShrink: 0, border: 'none',
          }}
        >
          {uploading ? 'Loading…' : '+ Import'}
        </button>

        <input
          ref={fileRef}
          type="file"
          accept={ACCEPTED}
          style={{ display: 'none' }}
          onChange={e => { handleFile(e.target.files[0]); e.target.value = '' }}
        />
      </div>

      {uploadErr && (
        <div style={{ padding: '6px 14px', fontSize: 11, color: 'var(--red)', flexShrink: 0 }}>
          {uploadErr}
        </div>
      )}

      {/* ── Sections — fills all remaining space ── */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '10px 10px 6px' }}>
        {sections.length === 0 ? (
          <div style={{
            color: 'var(--text-dim)', fontSize: 12,
            textAlign: 'center', marginTop: 48, lineHeight: 1.8,
          }}>
            Import a script<br />to see sections here
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {sections.map((sec, i) => (
              <button
                key={i}
                onClick={() => onSectionSeek(sec)}
                style={{
                  width: '100%', boxSizing: 'border-box',
                  background: 'var(--surface2)',
                  border: '1px solid var(--border)',
                  color: 'var(--text)',
                  padding: '7px 10px',
                  fontSize: 13, textAlign: 'left',
                  cursor: 'pointer', borderRadius: 4,
                  whiteSpace: 'normal', lineHeight: 1.3,
                  display: 'flex', flexDirection: 'column', gap: 2,
                }}
              >
                <span style={{ fontWeight: 600 }}>{sec.title}</span>
                {sec.summary && (
                  <span style={{ fontSize: 11, color: 'var(--text-dim)', fontWeight: 400, lineHeight: 1.4 }}>
                    {sec.summary}
                  </span>
                )}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* ── Bottom: Deepgram key ── */}
      <div style={{
        padding: '10px 12px 13px',
        borderTop: '1px solid var(--border)',
        flexShrink: 0,
      }}>
        {dgConnected ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 12, color: '#64b5f6' }}>
            <span style={{
              width: 7, height: 7, borderRadius: '50%',
              background: '#4caf50', display: 'inline-block', flexShrink: 0,
            }} />
            Deepgram Nova-3 connected
          </div>
        ) : (
          <>
            <input
              type="password"
              value={dgKey}
              onChange={e => setDgKey(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && connectDeepgram()}
              placeholder={connecting ? 'Connecting…' : 'Deepgram API key — press Enter'}
              style={{ width: '100%', boxSizing: 'border-box', fontSize: 12 }}
              disabled={connecting}
            />
            {dgError && (
              <div style={{ marginTop: 5, fontSize: 11, color: 'var(--red)' }}>
                Connection failed — check your key
              </div>
            )}
            {!dgError && (
              <div style={{ marginTop: 4, fontSize: 11, color: 'var(--text-dim)' }}>
                Never stored · ~$0.006/min
              </div>
            )}
          </>
        )}
      </div>

    </div>
  )
}
