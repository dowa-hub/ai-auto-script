import { useState, useRef, useCallback, useEffect } from 'react'

const WS_URL = 'ws://localhost:8000/ws/audio'
const RECONNECT_DELAY = 2000

/**
 * Manages the WebSocket connection to the backend.
 * Exposes sendChunk(ArrayBuffer) and listens for position/confidence updates.
 */
export function useScriptSocket(onMessage) {
  const [connected, setConnected]   = useState(false)
  const [modelReady, setModelReady] = useState(false)
  const wsRef     = useRef(null)
  const reconnRef = useRef(null)

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    const ws = new WebSocket(WS_URL)
    wsRef.current = ws

    ws.onopen = () => {
      setConnected(true)
      clearTimeout(reconnRef.current)
    }

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data)
        if (msg.type === 'model_status') {
          setModelReady(msg.status === 'ready')
        }
        if (typeof onMessage === 'function') onMessage(msg)
      } catch {}
    }

    ws.onclose = () => {
      setConnected(false)
      setModelReady(false)
      reconnRef.current = setTimeout(connect, RECONNECT_DELAY)
    }

    ws.onerror = () => ws.close()
  }, [onMessage])

  useEffect(() => {
    connect()
    return () => {
      clearTimeout(reconnRef.current)
      wsRef.current?.close()
    }
  }, [connect])

  const sendChunk = useCallback((buffer) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(buffer)
    }
  }, [])

  return { connected, modelReady, sendChunk }
}
