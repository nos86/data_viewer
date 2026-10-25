/** Upload → conversion → ready, as one visible process per file (RF-37). */
import { reactive } from 'vue'
import { api, uploadApi } from '../api/client'
import { CancelledError, uploadFile, type Phase, type UploadApi } from './upload'

export interface UploadItem {
  key: number
  name: string
  size: number
  phase: Phase
  sent: number
  resumedFrom: number
  totalChunks: number
  retry: number
  convPhase: string // phase reported by the conversion
  convProgress: number
  message: string | null
  measurementId: string | null
  uploadId: string | null
}

let counter = 0
const wait = (ms: number) => new Promise<void>((r) => setTimeout(r, ms))

export function useUploads(onReady: (id: string) => void) {
  const items = reactive<UploadItem[]>([])
  const controllers = new Map<number, AbortController>()

  async function convert(it: UploadItem, id: string) {
    it.phase = 'converting'
    it.measurementId = id
    let st = await api.startIngest(id)
    while (st.state === 'queued' || st.state === 'running') {
      it.convPhase = st.phase
      it.convProgress = st.progress
      await wait(700)
      if (!controllers.has(it.key)) throw new CancelledError('cancelled')
      st = await api.ingestStatus(id)
    }
    if (st.state === 'error') throw new Error(st.message ?? 'The file could not be converted')
    it.phase = 'ready'
    onReady(id)
  }

  async function run(file: File) {
    const it = reactive<UploadItem>({
      key: ++counter, name: file.name, size: file.size, phase: 'starting', sent: 0, resumedFrom: 0, totalChunks: 0, retry: 0,
      convPhase: '', convProgress: 0, message: null, measurementId: null, uploadId: null,
    })
    items.unshift(it)
    const ctl = new AbortController()
    controllers.set(it.key, ctl)
    const tracked: UploadApi = {
      ...uploadApi,
      create: async (b) => {
        const info = await uploadApi.create(b)
        it.uploadId = info.upload_id
        return info
      },
    }
    try {
      const done = await uploadFile(file, tracked, {
        signal: ctl.signal,
        onProgress: (p) => Object.assign(it, { phase: p.phase, sent: p.sent, resumedFrom: p.resumedFrom, totalChunks: p.totalChunks, retry: p.retry }),
      })
      it.sent = it.size
      if (done.existing) it.message = 'This file was already available: it is not converted again.'
      await convert(it, done.id)
    } catch (e) {
      if (e instanceof CancelledError) {
        it.phase = 'cancelled'
        it.message = 'Cancelled'
      } else {
        it.phase = 'error'
        it.message = (e instanceof Error ? e.message : String(e)) || 'The upload failed'
        if (e instanceof TypeError) it.message = 'Network error: select the same file again to resume the upload.'
      }
    } finally {
      controllers.delete(it.key)
    }
  }

  const start = (files: File[]) => files.forEach((f) => void run(f))

  async function cancel(it: UploadItem) {
    controllers.get(it.key)?.abort()
    controllers.delete(it.key)
    if (it.uploadId && it.phase !== 'converting' && it.phase !== 'ready') await uploadApi.cancel(it.uploadId).catch(() => undefined)
    if (it.phase !== 'ready') {
      it.phase = 'cancelled'
      it.message = 'Cancelled'
    }
  }

  const dismiss = (it: UploadItem) => {
    const i = items.indexOf(it)
    if (i >= 0) items.splice(i, 1)
  }

  return { items, start, cancel, dismiss }
}
