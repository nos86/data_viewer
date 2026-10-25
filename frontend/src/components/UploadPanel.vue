<script setup lang="ts">
import { ref } from 'vue'
import type { UploadItem } from '../composables/useUploads'
import { localeNumber } from '../composables/locale'

defineProps<{ items: UploadItem[] }>()
const emit = defineEmits<{ pick: [files: File[]]; cancel: [item: UploadItem]; dismiss: [item: UploadItem] }>()

const input = ref<HTMLInputElement>()
const over = ref(false)

const mb = (b: number) => localeNumber(b / 1048576, 1)
function onFiles(e: Event) {
  const el = e.target as HTMLInputElement
  if (el.files?.length) emit('pick', [...el.files])
  el.value = ''
}
function onDrop(e: DragEvent) {
  over.value = false
  if (e.dataTransfer?.files.length) emit('pick', [...e.dataTransfer.files])
}
defineExpose({ onDrop })

const label = (i: UploadItem) =>
  i.phase === 'starting' ? 'Starting…'
  : i.phase === 'uploading' ? `Uploading ${Math.round((i.sent / i.size) * 100)} % · ${mb(i.sent)} / ${mb(i.size)} MB${i.resumedFrom ? ` · resumed after block ${i.resumedFrom}/${i.totalChunks}` : ''}${i.retry ? ` · retry ${i.retry}` : ''}`
  : i.phase === 'finishing' ? 'Assembling the file…'
  : i.phase === 'converting' ? `Converting: ${i.convPhase || 'queued'}`
  : i.phase === 'ready' ? 'Ready'
  : i.message ?? i.phase
const pct = (i: UploadItem) => (i.phase === 'converting' ? i.convProgress : i.phase === 'ready' ? 1 : i.sent / i.size)
</script>

<template>
  <div class="up" :class="{ over }" @dragover.prevent="over = true" @dragleave="over = false" @drop.prevent="onDrop">
    <div class="row">
      <button title="Upload a measurement file from this computer" @click="input?.click()">Upload…</button>
      <span class="hint">or drop files here</span>
      <input ref="input" type="file" multiple hidden @change="onFiles" />
    </div>
    <ul v-if="items.length">
      <li v-for="i in items" :key="i.key" :class="i.phase">
        <div class="top">
          <span class="name" :title="i.name">{{ i.name }}</span>
          <button v-if="i.phase === 'starting' || i.phase === 'uploading' || i.phase === 'finishing'" title="Cancel and discard what was sent" @click="emit('cancel', i)">Cancel</button>
          <button v-else class="x" title="Remove from this list" @click="emit('dismiss', i)">×</button>
        </div>
        <progress v-if="i.phase !== 'error' && i.phase !== 'cancelled'" :value="pct(i)" max="1" />
        <div class="msg" :class="{ err: i.phase === 'error' }">{{ label(i) }}</div>
        <div v-if="i.message && i.phase !== 'error' && i.phase !== 'cancelled'" class="msg">{{ i.message }}</div>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.up { border: 1px dashed var(--border); border-radius: 6px; padding: 6px 8px; margin-bottom: 6px; background: var(--bg); }
.up.over { border-color: var(--accent); background: #eef6ff; }
.row { display: flex; align-items: center; gap: 8px; }
.hint { color: var(--muted); font-size: 12px; }
ul { list-style: none; margin: 6px 0 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
li { font-size: 12px; }
.top { display: flex; justify-content: space-between; gap: 6px; align-items: center; }
.name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 600; }
progress { width: 100%; height: 8px; }
.msg { color: var(--muted); }
.msg.err { color: var(--danger); white-space: normal; }
.x { border: none; background: none; color: var(--muted); font-size: 14px; }
</style>
