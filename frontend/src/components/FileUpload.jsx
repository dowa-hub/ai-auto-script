import { useState, useRef } from 'react'

const ACCEPTED = '.pdf,.docx,.doc,.xlsx,.xls,.txt,.jpg,.jpeg,.png,.tiff,.tif'

export default function FileUpload({ onScriptLoaded }) {
  const [dragging, setDragging] = useState(false)
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState('')
  const inputRef = useRef(null)

  async function upload(file) {
    if (!file) return
    setLoading(true)
    setError('')
    try {
      const form = new FormData()
      form.append('file', file)
      const res  = await fetch('/api/upload-script', { method: 'POST', body: form })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Upload failed')

      // Fetch parsed script
      const scriptRes  = await fetch('/api/script')
      const scriptData = await scriptRes.json()
      onScriptLoaded(scriptData)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  function onDrop(e) {
    e.preventDefault()
    setDragging(false)
    const file = e.dataTransfer.files[0]
    if (file) upload(file)
  }

  return (
    <div style={{ padding: '24px 0' }}>
      <div
        style={{
          border: `2px dashed ${dragging ? 'var(--amber)' : 'var(--border)'}`,
          borderRadius: 10,
          padding: '40px 24px',
          textAlign: 'center',
          cursor: 'pointer',
          transition: 'border-color 0.2s, background 0.2s',
          background: dragging ? 'var(--amber-dim)' : 'transparent',
        }}
        onClick={() => inputRef.current?.click()}
        onDragOver={e => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <div style={{ fontSize: 32, marginBottom: 10 }}>📄</div>
        <div style={{ fontWeight: 600, marginBottom: 6 }}>
          {loading ? 'Parsing script…' : 'Drop your script here'}
        </div>
        <div style={{ color: 'var(--text-dim)', fontSize: 12 }}>
          PDF · DOCX · XLSX · TXT · JPG / PNG (OCR)
        </div>
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPTED}
          style={{ display: 'none' }}
          onChange={e => upload(e.target.files[0])}
        />
      </div>
      {error && (
        <div style={{ color: 'var(--red)', fontSize: 12, marginTop: 8 }}>{error}</div>
      )}
    </div>
  )
}
