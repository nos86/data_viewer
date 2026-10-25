import { tableFromIPC } from 'apache-arrow'
import type { UploadDone, UploadInfo } from '../composables/upload'
import type { components } from './schema'

type S = components['schemas']
export type FileOut = S['FileOut']
export type Me = S['MeOut']
export type IngestStatus = S['IngestStatusOut']
export type ChannelOut = S['ChannelOut']
export type ChannelsOut = S['ChannelsOut']
export type Mode = 'step' | 'linear' | 'average'
export type SeriesFormat = 'json' | 'arrow'
export type OperatorMeta = S['OperatorOut']
export type OperatorResult = S['OperatorResult']
export type OperatorRequest = S['OperatorRequest']

/** One decoded series: values on the grid j = jStart.. (step n), NaN = null (hole). */
export interface SeriesData {
  channel: string
  mode: Mode
  n: number
  jStart: number
  values: Float64Array
}

export interface SeriesResult {
  stepBaseUs: number
  series: SeriesData[]
  timing: { format: SeriesFormat; fetchMs: number; decodeMs: number; bytes: number }
}

const BASE = '/api/v1'
const ARROW = 'application/vnd.apache.arrow.stream'

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public detail?: unknown, // structured error body, e.g. {code: 'unknown_handle', handle}
  ) {
    super(message)
  }
}

export const isUnknownHandle = (e: unknown) => e instanceof ApiError && (e.detail as { code?: string } | undefined)?.code === 'unknown_handle'

async function failure(r: Response): Promise<never> {
  let message = r.statusText
  let detail: unknown
  try {
    const body = await r.json()
    detail = body.detail
    message = typeof detail === 'string' ? detail : ((detail as { message?: string })?.message ?? JSON.stringify(detail))
  } catch {
    /* keep statusText */
  }
  throw new ApiError(r.status, message, detail)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(BASE + path, init)
  if (!r.ok) return failure(r)
  return r.json() as Promise<T>
}

const json = (body: unknown, headers: Record<string, string> = {}): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json', ...headers },
  body: JSON.stringify(body),
})

/** `?format=json|arrow` in the page URL selects the transfer format (default: see DEFAULT_FORMAT). */
export const DEFAULT_FORMAT: SeriesFormat = 'arrow'
export function currentFormat(): SeriesFormat {
  const f = new URLSearchParams(window.location.search).get('format')
  return f === 'json' || f === 'arrow' ? f : DEFAULT_FORMAT
}

export interface SeriesRequest {
  name: string
  mode: Mode
}

async function fetchSeries(id: string, channels: SeriesRequest[], format: SeriesFormat, window?: [number, number]): Promise<SeriesResult> {
  const body: Record<string, unknown> = { channels }
  if (window) Object.assign(body, { j_start: window[0], j_end: window[1] })
  const t0 = performance.now()
  const r = await fetch(`${BASE}/files/${id}/series`, json(body, format === 'arrow' ? { Accept: ARROW } : {}))
  if (!r.ok) return failure(r)
  if (format === 'arrow') {
    const meta = JSON.parse(r.headers.get('X-Series-Meta') ?? '{}')
    const buf = new Uint8Array(await r.arrayBuffer())
    const t1 = performance.now()
    const table = tableFromIPC(buf)
    const series: SeriesData[] = table.batches.map((b, i) => {
      const m = meta.series[i]
      // nulls keep NaN in the data buffer (written that way by the server): no per-value pass needed
      return { channel: m.channel, mode: m.mode, n: m.n, jStart: m.j_start, values: b.getChildAt(0)!.toArray() as Float64Array }
    })
    return { stepBaseUs: meta.step_base_us, series, timing: { format, fetchMs: t1 - t0, decodeMs: performance.now() - t1, bytes: buf.byteLength } }
  }
  const text = await r.text()
  const t1 = performance.now()
  const data = JSON.parse(text) as S['SeriesResponse']
  const series: SeriesData[] = data.series.map((s) => {
    const v = new Float64Array(s.values.length)
    for (let i = 0; i < v.length; i++) v[i] = s.values[i] ?? NaN
    return { channel: s.channel, mode: s.mode, n: s.n, jStart: s.j_start, values: v }
  })
  return { stepBaseUs: data.step_base_us, series, timing: { format, fetchMs: t1 - t0, decodeMs: performance.now() - t1, bytes: text.length } }
}

/** Result of POST /operators/{name}: summary (stats, sparkline) and, with detail 'full', the series. */
export interface OperatorOutcome {
  result: Omit<OperatorResult, 'series'>
  series?: SeriesData
}

async function applyOperator(name: string, body: OperatorRequest, format: SeriesFormat = currentFormat()): Promise<OperatorOutcome> {
  const full = body.detail === 'full'
  const r = await fetch(`${BASE}/operators/${name}`, json(body, full && format === 'arrow' ? { Accept: ARROW } : {}))
  if (!r.ok) return failure(r)
  if (full && format === 'arrow') {
    const meta = JSON.parse(r.headers.get('X-Series-Meta') ?? '{}')
    const table = tableFromIPC(new Uint8Array(await r.arrayBuffer()))
    const m = meta.series[0]
    const values = table.batches[0].getChildAt(0)!.toArray() as Float64Array
    return { result: meta.result, series: { channel: m.channel, mode: m.mode, n: m.n, jStart: m.j_start, values } }
  }
  const data = (await r.json()) as OperatorResult
  const { series, ...result } = data
  let sd: SeriesData | undefined
  if (series) {
    const v = new Float64Array(series.values.length)
    for (let i = 0; i < v.length; i++) v[i] = series.values[i] ?? NaN
    sd = { channel: series.channel, mode: series.mode, n: series.n, jStart: series.j_start, values: v }
  }
  return { result, series: sd }
}

/** One upload block over XHR so that the progress inside a 50 MB block is visible. */
function putChunk(uid: string, index: number, data: ArrayBuffer, sha256: string, onBytes: (sent: number) => void): Promise<void> {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest()
    x.open('PUT', `${BASE}/uploads/${uid}/chunks/${index}`)
    x.setRequestHeader('X-Chunk-SHA256', sha256)
    x.upload.onprogress = (e) => onBytes(e.loaded)
    x.onload = () => {
      if (x.status >= 200 && x.status < 300) return resolve()
      let detail: unknown
      let message = x.statusText
      try {
        detail = JSON.parse(x.responseText).detail
        message = typeof detail === 'string' ? detail : ((detail as { message?: string })?.message ?? message)
      } catch {
        /* keep statusText */
      }
      reject(new ApiError(x.status, message, detail))
    }
    x.onerror = () => reject(new TypeError('Network error'))
    x.send(data)
  })
}

export const uploadApi = {
  create: (body: { filename: string; size: number; fingerprint: string }) => request<UploadInfo>('/uploads', json(body)),
  putChunk,
  complete: (uid: string) => request<UploadDone>(`/uploads/${uid}/complete`, { method: 'POST' }),
  cancel: async (uid: string) => void (await fetch(`${BASE}/uploads/${uid}`, { method: 'DELETE' })),
}

export interface StorageInfo {
  used_bytes: number
  limit_bytes: number
  uploads_bytes: number
  parquet_bytes: number
  measurements: number
}

export interface ExportRequest {
  format: 'csv' | 'parquet'
  series: { label: string; file?: string; channel?: string; mode?: Mode; handle?: string }[]
  k_from: number | null
  k_to: number | null
  shifts: Record<string, number>
  csv?: { decimal: '.' | ','; delimiter: ',' | ';' | '	' }
  filename?: string
}

/** Streams the export into a Blob; errors keep the structured detail (unknown_handle...). */
async function exportData(body: ExportRequest): Promise<Blob> {
  const r = await fetch(`${BASE}/export`, json(body))
  if (!r.ok) return failure(r)
  return r.blob()
}

export const api = {
  storage: () => request<StorageInfo>('/storage'),
  pin: (ids: string[]) => fetch(`${BASE}/sessions/pin`, json({ ids })).then(() => undefined),
  exportData,

  operators: () => request<OperatorMeta[]>('/operators'),
  applyOperator,
  listFiles: (query = '') => request<FileOut[]>('/files' + query),
  me: () => request<Me>('/me'),
  startIngest: (id: string) => request<IngestStatus>(`/files/${id}/ingest`, { method: 'POST' }),
  ingestStatus: (id: string) => request<IngestStatus>(`/files/${id}/ingest`),
  channels: (id: string) => request<ChannelsOut>(`/files/${id}/channels`),
  series: (id: string, channels: SeriesRequest[], format: SeriesFormat = currentFormat()) => fetchSeries(id, channels, format),
}
