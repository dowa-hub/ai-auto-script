import { useState, useRef, useCallback, useEffect } from 'react'

/**
 * Manages audio capture from a user-selected input device.
 * Captures at the device's native sample rate (up to 96kHz),
 * downsamples to 16kHz mono via AudioWorklet, then calls
 * onChunk(ArrayBuffer) with each 250ms PCM int16 chunk.
 */
export function useAudio(onChunk) {
  const [devices, setDevices]       = useState([])
  const [deviceId, setDeviceId]     = useState('')
  const [isCapturing, setCapturing] = useState(false)
  const [inputRate, setInputRate]   = useState(null)   // actual device sample rate
  const [level, setLevel]           = useState(0)      // 0-1 RMS level

  const ctxRef      = useRef(null)
  const streamRef   = useRef(null)
  const workletRef  = useRef(null)
  const analyserRef = useRef(null)
  const rafRef      = useRef(null)

  // Enumerate input devices (requires mic permission first)
  const refreshDevices = useCallback(async () => {
    try {
      // Trigger permission prompt if needed
      const tmp = await navigator.mediaDevices.getUserMedia({ audio: true })
      tmp.getTracks().forEach(t => t.stop())

      const all = await navigator.mediaDevices.enumerateDevices()
      const inputs = all.filter(d => d.kind === 'audioinput')
      setDevices(inputs)
      if (!deviceId && inputs.length > 0) setDeviceId(inputs[0].deviceId)
    } catch (e) {
      console.error('Device enumeration failed:', e)
    }
  }, [deviceId])

  useEffect(() => {
    refreshDevices()
  }, [])

  const start = useCallback(async (selectedDeviceId) => {
    const id = selectedDeviceId || deviceId
    try {
      // Request audio — disable all processing so we get a clean feed
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          deviceId: id ? { exact: id } : undefined,
          sampleRate:         { ideal: 48000, max: 96000 },
          channelCount:       { ideal: 1 },
          echoCancellation:   false,
          noiseSuppression:   false,
          autoGainControl:    false,
        },
      })
      streamRef.current = stream

      // AudioContext adopts the device's native sample rate automatically
      const ctx = new AudioContext()
      ctxRef.current = ctx
      setInputRate(ctx.sampleRate)

      // Load the worklet
      await ctx.audioWorklet.addModule('/processor.worklet.js')

      const worklet = new AudioWorkletNode(ctx, 'audio-resample-processor')
      workletRef.current = worklet

      worklet.port.onmessage = (e) => {
        if (typeof onChunk === 'function') onChunk(e.data)
      }

      // Analyser for level meter
      const analyser = ctx.createAnalyser()
      analyser.fftSize = 256
      analyserRef.current = analyser

      const source = ctx.createMediaStreamSource(stream)
      source.connect(analyser)
      source.connect(worklet)

      setCapturing(true)
      _startLevelMeter(analyser)
    } catch (e) {
      console.error('Audio capture failed:', e)
      throw e
    }
  }, [deviceId, onChunk])

  const stop = useCallback(() => {
    cancelAnimationFrame(rafRef.current)
    workletRef.current?.disconnect()
    streamRef.current?.getTracks().forEach(t => t.stop())
    ctxRef.current?.close()
    ctxRef.current = null
    streamRef.current = null
    workletRef.current = null
    analyserRef.current = null
    setCapturing(false)
    setLevel(0)
    setInputRate(null)
  }, [])

  function _startLevelMeter(analyser) {
    const data = new Uint8Array(analyser.frequencyBinCount)
    const tick = () => {
      analyser.getByteTimeDomainData(data)
      let sum = 0
      for (let i = 0; i < data.length; i++) {
        const v = (data[i] - 128) / 128
        sum += v * v
      }
      setLevel(Math.sqrt(sum / data.length))
      rafRef.current = requestAnimationFrame(tick)
    }
    rafRef.current = requestAnimationFrame(tick)
  }

  return { devices, deviceId, setDeviceId, isCapturing, inputRate, level, start, stop, refreshDevices }
}
