/** Chunked, resumable upload of a binary measurement (RI-05, RF-37). The browser only slices, hashes and sends blocks:
 * which blocks are still missing is always decided by the server, so an interrupted upload resumes by selecting the same file again. */

export interface UploadFile {
  name: string
  size: number
  lastModified: number
  slice(start: number, end: number): { arrayBuffer(): Promise<ArrayBuffer> }
}

export interface UploadInfo {
  upload_id: string
  chunk_size: number
  total_chunks: number
  received: number[]
}

export interface UploadDone {
  id: string
  name: string
  size: number
  existing: boolean
}

export interface UploadApi {
  create(body: { filename: string; size: number; fingerprint: string }): Promise<UploadInfo>
  putChunk(uid: string, index: number, data: ArrayBuffer, sha256: string, onBytes: (sent: number) => void): Promise<void>
  complete(uid: string): Promise<UploadDone>
  cancel(uid: string): Promise<void>
}

export type Phase = 'starting' | 'uploading' | 'finishing' | 'converting' | 'ready' | 'error' | 'cancelled'

export interface UploadProgress {
  phase: Phase
  sent: number // bytes confirmed by the server, plus the part of the current block already on the wire
  total: number
  resumedFrom: number // blocks the server already had when this attempt started
  totalChunks: number
  chunk: number // block being sent
  retry: number // attempt of the current block (0 = first)
}

export interface UploadOptions {
  onProgress?: (p: UploadProgress) => void
  signal?: AbortSignal
  retries?: number
  sleep?: (ms: number) => Promise<void>
  sha256?: (data: ArrayBuffer) => Promise<string>
}

/** Failures that retrying cannot fix (limit exceeded, unknown upload, empty file...). */
export class FatalUploadError extends Error {}
export class CancelledError extends Error {}

export const fingerprint = (f: Pick<UploadFile, 'name' | 'size' | 'lastModified'>) => `${f.name}|${f.size}|${f.lastModified}`

export const missingChunks = (total: number, received: number[]): number[] => {
  const have = new Set(received)
  return Array.from({ length: total }, (_, i) => i).filter((i) => !have.has(i))
}

export const chunkRange = (info: Pick<UploadInfo, 'chunk_size'>, size: number, index: number): [number, number] => [
  index * info.chunk_size,
  Math.min(size, (index + 1) * info.chunk_size),
]

export async function sha256Hex(data: ArrayBuffer): Promise<string> {
  const d = await crypto.subtle.digest('SHA-256', data)
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, '0')).join('')
}

const wait = (ms: number) => new Promise<void>((r) => setTimeout(r, ms))

/** HTTP status carried by an error, if any (ApiError has `status`). */
const statusOf = (e: unknown) => (e as { status?: number })?.status

export async function uploadFile(file: UploadFile, api: UploadApi, o: UploadOptions = {}): Promise<UploadDone> {
  const retries = o.retries ?? 3
  const sleep = o.sleep ?? wait
  const sha = o.sha256 ?? sha256Hex
  const check = () => {
    if (o.signal?.aborted) throw new CancelledError('Upload cancelled')
  }
  const progress: UploadProgress = { phase: 'starting', sent: 0, total: file.size, resumedFrom: 0, totalChunks: 0, chunk: 0, retry: 0 }
  const report = () => o.onProgress?.({ ...progress })
  report()

  let info: UploadInfo
  try {
    info = await api.create({ filename: file.name, size: file.size, fingerprint: fingerprint(file) })
  } catch (e) {
    throw statusOf(e) && statusOf(e)! < 500 ? new FatalUploadError((e as Error).message) : e
  }
  progress.totalChunks = info.total_chunks
  progress.resumedFrom = info.received.length
  let confirmed = info.received.reduce((s, i) => {
    const [a, b] = chunkRange(info, file.size, i)
    return s + (b - a)
  }, 0)
  progress.sent = confirmed
  progress.phase = 'uploading'
  report()

  for (const i of missingChunks(info.total_chunks, info.received)) {
    const [a, b] = chunkRange(info, file.size, i)
    const data = await file.slice(a, b).arrayBuffer()
    const hash = await sha(data)
    for (let attempt = 0; ; attempt++) {
      check()
      progress.chunk = i
      progress.retry = attempt
      try {
        await api.putChunk(info.upload_id, i, data, hash, (sent) => {
          progress.sent = confirmed + sent
          report()
        })
        break
      } catch (e) {
        const st = statusOf(e)
        const corrupted = st === 422 // damaged in transit: sending it again is the cure
        if ((st && st < 500 && !corrupted) || attempt >= retries) {
          if (st && st < 500 && !corrupted) throw new FatalUploadError((e as Error).message)
          throw new Error(`Block ${i + 1} of ${info.total_chunks} failed after ${attempt + 1} attempts: ${(e as Error).message}. Select the same file again to resume.`)
        }
        await sleep(500 * 2 ** attempt)
      }
    }
    confirmed += b - a
    progress.sent = confirmed
    report()
  }

  check()
  progress.phase = 'finishing'
  report()
  try {
    return await api.complete(info.upload_id)
  } catch (e) {
    if (statusOf(e) === 409) throw new Error(`${(e as Error).message}. Select the same file again to resume.`)
    throw e
  }
}
