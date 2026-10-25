import { describe, expect, it } from 'vitest'
import { CancelledError, FatalUploadError, chunkRange, fingerprint, missingChunks, uploadFile, type UploadApi, type UploadFile, type UploadInfo, type UploadProgress } from './upload'

class StatusError extends Error {
  constructor(public status: number, m: string) {
    super(m)
  }
}

const file = (size: number, name = 'run.bin'): UploadFile => ({
  name, size, lastModified: 1234,
  slice: (a, b) => ({ arrayBuffer: async () => new Uint8Array(Array.from({ length: b - a }, (_, i) => (a + i) % 251)).buffer }),
})

/** In-memory server with the same contract as the real one. */
function server(chunkSize = 10, opts: { failures?: Record<number, (n: number) => Error | null>; preReceived?: number[] } = {}) {
  const got = new Map<number, number>(opts.preReceived?.map((i) => [i, 0]) ?? [])
  const attempts: Record<number, number> = {}
  const log: string[] = []
  let completed = false
  const api: UploadApi = {
    async create(b) {
      log.push(`create ${b.filename} ${b.size} ${b.fingerprint}`)
      return { upload_id: 'u1', chunk_size: chunkSize, total_chunks: Math.ceil(b.size / chunkSize), received: [...got.keys()].sort((x, y) => x - y) } as UploadInfo
    },
    async putChunk(_u, i, data, sha, onBytes) {
      attempts[i] = (attempts[i] ?? 0) + 1
      const f = opts.failures?.[i]?.(attempts[i])
      if (f) throw f
      onBytes(data.byteLength / 2)
      onBytes(data.byteLength)
      expect(sha).toBe(`sha${data.byteLength}`)
      got.set(i, data.byteLength)
      log.push(`put ${i}`)
    },
    async complete() {
      completed = true
      const missing = [...Array(Math.ceil(100 / chunkSize)).keys()].filter((i) => !got.has(i))
      void missing
      return { id: 'abc', name: 'run.bin', size: 0, existing: false }
    },
    async cancel() {
      log.push('cancel')
    },
  }
  return { api, got, attempts, log, done: () => completed }
}

const fastOpts = (extra = {}) => ({ sleep: async () => {}, sha256: async (d: ArrayBuffer) => `sha${d.byteLength}`, ...extra })

describe('plan helpers', () => {
  it('fingerprint identifies the file, missing chunks are the complement of what the server has', () => {
    expect(fingerprint({ name: 'a.bin', size: 5, lastModified: 7 })).toBe('a.bin|5|7')
    expect(missingChunks(5, [0, 2, 4])).toEqual([1, 3])
    expect(missingChunks(3, [])).toEqual([0, 1, 2])
    expect(missingChunks(2, [0, 1])).toEqual([])
  })
  it('chunk ranges cover the file exactly, the last one shorter', () => {
    const info = { chunk_size: 10 }
    expect([0, 1, 2].map((i) => chunkRange(info, 25, i))).toEqual([[0, 10], [10, 20], [20, 25]])
  })
})

describe('upload (RI-05, RF-37)', () => {
  it('sends every block in order, reports progress and completes', async () => {
    const s = server()
    const seen: UploadProgress[] = []
    const r = await uploadFile(file(25), s.api, fastOpts({ onProgress: (p: UploadProgress) => seen.push(p) }))
    expect(r.id).toBe('abc')
    expect(s.log.filter((l) => l.startsWith('put'))).toEqual(['put 0', 'put 1', 'put 2'])
    expect(s.log[0]).toBe('create run.bin 25 run.bin|25|1234')
    expect(seen[0].phase).toBe('starting')
    expect(seen.at(-1)!.phase).toBe('finishing')
    const sent = seen.filter((p) => p.phase === 'uploading').map((p) => p.sent)
    expect(sent).toEqual([...sent].sort((a, b) => a - b))                       // never goes backwards
    expect(Math.max(...sent)).toBe(25)
    expect(seen.at(-1)).toMatchObject({ totalChunks: 3, resumedFrom: 0, total: 25 })
  })

  it('resumes: only the blocks the server does not have are sent, progress starts from what it has', async () => {
    const s = server(10, { preReceived: [0, 1] })
    const seen: UploadProgress[] = []
    await uploadFile(file(25), s.api, fastOpts({ onProgress: (p: UploadProgress) => seen.push(p) }))
    expect(s.log.filter((l) => l.startsWith('put'))).toEqual(['put 2'])
    const first = seen.find((p) => p.phase === 'uploading')!
    expect(first.sent).toBe(20)
    expect(first.resumedFrom).toBe(2)
  })

  it('retries a failed block with backoff and then succeeds', async () => {
    const waits: number[] = []
    const s = server(10, { failures: { 1: (n) => (n < 3 ? new TypeError('network down') : null) } })
    await uploadFile(file(25), s.api, fastOpts({ sleep: async (ms: number) => void waits.push(ms) }))
    expect(s.attempts[1]).toBe(3)
    expect(waits).toEqual([500, 1000])
    expect(s.attempts[0]).toBe(1)
  })

  it('a block damaged in transit (422) is sent again', async () => {
    const s = server(10, { failures: { 0: (n) => (n === 1 ? new StatusError(422, 'Block 0 was corrupted in transit') : null) } })
    await uploadFile(file(15), s.api, fastOpts())
    expect(s.attempts[0]).toBe(2)
  })

  it('gives up after the allowed attempts and says how to resume', async () => {
    const s = server(10, { failures: { 1: () => new TypeError('network down') } })
    await expect(uploadFile(file(25), s.api, fastOpts({ retries: 2 }))).rejects.toThrow(/Block 2 of 3 failed after 3 attempts.*resume/)
    expect(s.got.has(0) && !s.got.has(1)).toBe(true)                                // what was sent stays on the server
  })

  it('fatal errors are not retried (limit, unknown upload)', async () => {
    const s = server(10, { failures: { 0: () => new StatusError(404, 'Unknown upload') } })
    await expect(uploadFile(file(15), s.api, fastOpts())).rejects.toBeInstanceOf(FatalUploadError)
    expect(s.attempts[0]).toBe(1)
    const big: UploadApi = { ...server().api, create: async () => { throw new StatusError(413, 'The file is 5,000 MB; the largest accepted upload is 2,048 MB') } }
    await expect(uploadFile(file(15), big, fastOpts())).rejects.toThrow(/largest accepted upload/)
    await expect(uploadFile(file(15), big, fastOpts())).rejects.toBeInstanceOf(FatalUploadError)
  })

  it('server trouble at creation is reported as it is, not as a user error', async () => {
    const api: UploadApi = { ...server().api, create: async () => { throw new StatusError(503, 'unavailable') } }
    const e = await uploadFile(file(15), api, fastOpts()).catch((x) => x)
    expect(e).not.toBeInstanceOf(FatalUploadError)
  })

  it('can be cancelled between blocks', async () => {
    const s = server()
    const ctl = new AbortController()
    const api: UploadApi = { ...s.api, putChunk: async (...a) => { await s.api.putChunk(...a); ctl.abort() } }
    await expect(uploadFile(file(35), api, fastOpts({ signal: ctl.signal }))).rejects.toBeInstanceOf(CancelledError)
    expect(s.log.filter((l) => l.startsWith('put'))).toEqual(['put 0'])
  })

  it('an incomplete assembly is explained (409) and resumable', async () => {
    const s = server()
    const api: UploadApi = { ...s.api, complete: async () => { throw new StatusError(409, '1 of 3 blocks are missing') } }
    await expect(uploadFile(file(25), api, fastOpts())).rejects.toThrow(/missing.*resume/)
  })
})
