import { useState, useCallback } from 'react'
import { useAudio }        from './hooks/useAudio.js'
import { useScriptSocket } from './hooks/useScriptSocket.js'
import DocumentViewer from './components/DocumentViewer.jsx'
import AudioControls  from './components/AudioControls.jsx'
import StatusBar      from './components/StatusBar.jsx'
import Sidebar        from './components/Sidebar.jsx'

export default function App() {
  const [script,      setScript]      = useState(null)
  const [fileInfo,    setFileInfo]    = useState(null)  // { name } for DocumentViewer
  const [sections,    setSections]    = useState([])
  const [currentLine, setCurrentLine] = useState(null)
  const [confidence,     setConfidence]     = useState(0)
  const [transcript,     setTranscript]     = useState('')
  const [locked,         setLocked]         = useState(false)
  const [trackerStatus,  setTrackerStatus]  = useState({ state: 'idle', confidence: 0, missCount: 0 })

  // ── WebSocket ──────────────────────────────────────────────────────────────
  const onMessage = useCallback((msg) => {
    if (msg.type === 'transcript') {
      setTranscript(msg.text)
    }
    if (msg.type === 'position') {
      setCurrentLine(msg.line_index)
      setConfidence(msg.confidence)
      setTrackerStatus({ state: 'tracking', confidence: msg.confidence, missCount: 0 })
    }
    if (msg.type === 'tracker_status') {
      setConfidence(msg.confidence ?? 0)
      setTrackerStatus({ state: msg.state, confidence: msg.confidence ?? 0, missCount: msg.miss_count ?? 0 })
    }
  }, [])

  const { connected, modelReady, sttStatus, sendChunk, reconnect } = useScriptSocket(onMessage)

  // ── Audio ──────────────────────────────────────────────────────────────────
  const onChunk = useCallback((buffer) => {
    sendChunk(buffer)
  }, [sendChunk])

  const {
    devices, deviceId, setDeviceId,
    isCapturing, inputRate, level,
    start, stop,
  } = useAudio(onChunk)

  // ── Operator controls ──────────────────────────────────────────────────────
  async function handleSeek(wordIndex) {
    setLocked(true)
    try {
      await fetch('/api/seek', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ word_index: wordIndex }),
      })
      if (script) {
        const word = script.words[wordIndex]
        if (word) setCurrentLine(word.line_index)
      }
    } catch { setLocked(false) }
  }

  async function handleSectionSeek(wordIndex) {
    setLocked(true)
    try {
      await fetch('/api/seek-confirmed', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ word_index: wordIndex }),
      })
      if (script) {
        const word = script.words[wordIndex]
        if (word) setCurrentLine(word.line_index)
      }
    } catch { setLocked(false) }
  }

  async function handleResume() {
    setLocked(false)
    try { await fetch('/api/resume', { method: 'POST' }) } catch {}
  }

  async function handleReset() {
    setLocked(false)
    setCurrentLine(null)
    setConfidence(0)
    setTranscript('')
    setTrackerStatus({ state: 'idle', confidence: 0, missCount: 0 })
    try { await fetch('/api/reset', { method: 'POST' }) } catch {}
  }

  async function handleSettingsChange(changes) {
    const res = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(changes),
    })
    if (!res.ok) throw new Error('Settings update failed')
    reconnect()
  }

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden' }}>
      {/* Top status bar */}
      <StatusBar
        transcript={transcript}
        confidence={confidence}
        locked={locked}
        trackerStatus={trackerStatus}
        currentLine={currentLine}
        lineCount={script?.line_count}
        onResume={handleResume}
        onReset={handleReset}
      />

      {/* Main area: sidebar + script */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        <Sidebar
          onScriptLoaded={(data, name) => { setScript(data); setFileInfo({ name, ts: Date.now() }); setSections(data.sections ?? []) }}
          onSettingsChange={handleSettingsChange}
          sttStatus={sttStatus}
          sections={sections}
          onSectionSeek={handleSectionSeek}
        />

        <DocumentViewer
          fileInfo={fileInfo}
          currentLine={currentLine}
          locked={locked}
          scriptData={script}
          onSeek={handleSeek}
        />
      </div>

      {/* Bottom audio controls */}
      <AudioControls
        devices={devices}
        deviceId={deviceId}
        setDeviceId={setDeviceId}
        isCapturing={isCapturing}
        inputRate={inputRate}
        level={level}
        onStart={() => start(deviceId)}
        onStop={stop}
        connected={connected}
        modelReady={modelReady}
        sttStatus={sttStatus}
      />
    </div>
  )
}
