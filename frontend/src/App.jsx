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
  const [currentLine, setCurrentLine] = useState(null)
  const [confidence,  setConfidence]  = useState(0)
  const [transcript,  setTranscript]  = useState('')
  const [locked,      setLocked]      = useState(false)
  const [sttMode,     setSttMode]     = useState('offline')
  const [whisperModel,setWhisperModel]= useState('base.en')

  // ── WebSocket ──────────────────────────────────────────────────────────────
  const onMessage = useCallback((msg) => {
    if (msg.type === 'transcript') {
      setTranscript(msg.text)
    }
    if (msg.type === 'position') {
      setCurrentLine(msg.line_index)
      setConfidence(msg.confidence)
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
    await fetch('/api/seek', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ word_index: wordIndex }),
    })
    // Update line display immediately
    if (script) {
      const word = script.words[wordIndex]
      if (word) setCurrentLine(word.line_index)
    }
  }

  async function handleResume() {
    setLocked(false)
    await fetch('/api/resume', { method: 'POST' })
  }

  async function handleReset() {
    setLocked(false)
    setCurrentLine(null)
    setConfidence(0)
    setTranscript('')
    await fetch('/api/reset', { method: 'POST' })
  }

  async function handleSettingsChange(changes) {
    if (changes.stt_mode)      setSttMode(changes.stt_mode)
    if (changes.whisper_model) setWhisperModel(changes.whisper_model)
    await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(changes),
    })
    // Reconnect WebSocket so the new STT engine/mode takes effect immediately
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
        currentLine={currentLine}
        lineCount={script?.line_count}
        onResume={handleResume}
        onReset={handleReset}
      />

      {/* Main area: sidebar + script */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        <Sidebar
          onScriptLoaded={(data, name) => { setScript(data); setFileInfo({ name }) }}
          sttMode={sttMode}
          whisperModel={whisperModel}
          onSettingsChange={handleSettingsChange}
          sttStatus={sttStatus}
        />

        <DocumentViewer
          fileInfo={fileInfo}
          currentLine={currentLine}
          locked={locked}
          scriptData={script}
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
