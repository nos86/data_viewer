/** Open measurements: conversion status, channel catalogue and series per measurement (RF-01). Shifts live in the OpenMeasure. */
import { reactive, ref, shallowRef } from 'vue'
import { api, type ChannelOut, type FileOut, type IngestStatus, type SeriesData } from '../api/client'
import { closeMeasure, makePrimary, openMeasure, type OpenMeasure } from './measures'
import { seriesKey, type SeriesRef } from './layout'

export interface Runtime {
  status: IngestStatus | null
  channels: ChannelOut[]
  complete: boolean
}

const finished = (s: IngestStatus | null) => !!s && (s.state === 'done' || s.state === 'error')

export function useMeasurements() {
  const open = ref<OpenMeasure[]>([])
  const runtime = reactive<Record<string, Runtime>>({})
  const selectedId = ref<string | null>(null)
  const stepBaseUs = ref(100_000)
  const seriesMap = shallowRef(new Map<string, SeriesData>())
  const loading = ref(false)
  const error = ref<string | null>(null)
  const timers = new Map<string, number>()
  const pending = new Set<string>()
  const msg = (e: unknown) => (e instanceof Error ? e.message : String(e))

  function stopPolling(id?: string) {
    for (const [k, t] of [...timers]) {
      if (id === undefined || id === k) {
        window.clearInterval(t)
        timers.delete(k)
      }
    }
  }

  /** Opens (or just selects) a measurement; already converted ones come back as 'done' without a new conversion. */
  async function openFile(f: FileOut, shiftK = 0, onFinished?: () => void): Promise<OpenMeasure | null> {
    const existing = open.value.find((m) => m.id === f.id)
    if (existing) {
      selectedId.value = f.id
      return existing
    }
    const m = openMeasure(open.value, f.id, f.name, shiftK)
    if (!m) {
      error.value = 'At most 4 measurements can be open together. Close one first.'
      return null
    }
    selectedId.value = f.id
    runtime[f.id] = { status: null, channels: [], complete: false }
    error.value = null
    try {
      runtime[f.id].status = await api.startIngest(f.id)
    } catch (e) {
      error.value = msg(e)
      return m
    }
    const tick = async () => {
      if (!open.value.some((x) => x.id === f.id)) return stopPolling(f.id)
      try {
        const st = await api.ingestStatus(f.id)
        const ch = await api.channels(f.id)
        if (!runtime[f.id]) return
        runtime[f.id] = { status: st, channels: ch.channels, complete: ch.complete }
        // through the reactive list: `m` is the raw object and writing to it would not notify the shared-axis computations
        const live = open.value.find((x) => x.id === f.id)
        if (st.state === 'done' && live) live.durationUs = st.duration_us ?? null
      } catch (e) {
        error.value = msg(e)
      }
      if (finished(runtime[f.id]?.status ?? null)) {
        stopPolling(f.id)
        onFinished?.()
      }
    }
    await tick()
    if (open.value.some((x) => x.id === f.id) && !finished(runtime[f.id]?.status ?? null)) timers.set(f.id, window.setInterval(tick, 700))
    return m
  }

  /** Returns the shift delta when the primary changed (cursors and zoom must follow). */
  function close(id: string): number {
    stopPolling(id)
    const delta = closeMeasure(open.value, id)
    delete runtime[id]
    const next = new Map(seriesMap.value)
    for (const k of [...next.keys()]) if (k.startsWith(id + '|')) next.delete(k)
    seriesMap.value = next
    if (selectedId.value === id) selectedId.value = open.value[0]?.id ?? null
    return delta
  }

  const promote = (id: string) => makePrimary(open.value, id)

  async function ensureSeries(required: SeriesRef[]) {
    const need = required.filter((r) => {
      const k = seriesKey(r.fileId, r.channel, r.mode)
      return runtime[r.fileId]?.status?.state === 'done' && !seriesMap.value.has(k) && !pending.has(k)
    })
    if (!need.length) return
    need.forEach((r) => pending.add(seriesKey(r.fileId, r.channel, r.mode)))
    loading.value = true
    try {
      const byFile = new Map<string, SeriesRef[]>()
      for (const r of need) byFile.set(r.fileId, [...(byFile.get(r.fileId) ?? []), r])
      await Promise.all(
        [...byFile].map(async ([fileId, refs]) => {
          const r = await api.series(fileId, refs.map((x) => ({ name: x.channel, mode: x.mode })))
          if (!open.value.some((m) => m.id === fileId)) return
          stepBaseUs.value = r.stepBaseUs
          const next = new Map(seriesMap.value)
          for (const s of r.series) next.set(seriesKey(fileId, s.channel, s.mode), s)
          seriesMap.value = next
        }),
      )
      error.value = null
    } catch (e) {
      error.value = msg(e)
    } finally {
      need.forEach((r) => pending.delete(seriesKey(r.fileId, r.channel, r.mode)))
      loading.value = pending.size > 0
    }
  }

  function setSeries(key: string, s: SeriesData) {
    const next = new Map(seriesMap.value)
    next.set(key, s)
    seriesMap.value = next
  }

  function reset() {
    stopPolling()
    open.value = []
    for (const k of Object.keys(runtime)) delete runtime[k]
    seriesMap.value = new Map()
    pending.clear()
    selectedId.value = null
  }

  return { open, runtime, selectedId, stepBaseUs, seriesMap, loading, error, openFile, close, promote, ensureSeries, setSeries, reset, stopPolling }
}
