<script setup lang="ts">
import type { Runtime } from '../composables/useMeasurements'
import { shiftSeconds, type OpenMeasure } from '../composables/measures'

defineProps<{
  list: OpenMeasure[]
  runtime: Record<string, Runtime>
  selectedId: string | null
  stepBaseUs: number
  canT0: boolean // there is an active cursor
}>()
const emit = defineEmits<{
  select: [id: string]
  close: [id: string]
  primary: [id: string]
  shift: [id: string, seconds: number]
  t0: [id: string]
}>()

const state = (rt: Runtime | undefined) => rt?.status?.state ?? 'queued'
const seconds = (e: Event) => Number((e.target as HTMLInputElement).value)
</script>

<template>
  <ul class="open">
    <li v-for="(m, i) in list" :key="m.id" :class="{ sel: m.id === selectedId }" @click="emit('select', m.id)">
      <div class="top">
        <span class="lab">{{ m.label }}</span>
        <span class="name" :title="m.name">{{ m.name }}</span>
        <span v-if="i === 0" class="badge" title="Primary: its time is the cursor time">primary</span>
        <span class="badge" :class="state(runtime[m.id])">{{ state(runtime[m.id]) }}</span>
        <button class="x" title="Close this measurement" @click.stop="emit('close', m.id)">×</button>
      </div>
      <div v-if="i > 0" class="shift" @click.stop>
        <label>Shift (s)
          <input type="number" step="0.1" :value="shiftSeconds(m, stepBaseUs).toFixed(1)" @change="emit('shift', m.id, seconds($event))" />
        </label>
        <button :disabled="!canT0" title="Move the start of this measurement to the active cursor (T)" @click="emit('t0', m.id)">t0 → cursor</button>
        <button title="Use this measurement as the time reference of the cursors" @click="emit('primary', m.id)">Make primary</button>
      </div>
    </li>
    <li v-if="!list.length" class="none">Click a measurement above to open it (up to 4).</li>
  </ul>
</template>

<style scoped>
.open { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
li { border: 1px solid var(--border); border-radius: 6px; background: var(--bg); padding: 4px 6px; cursor: pointer; }
li.sel { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent) inset; }
.top { display: flex; align-items: center; gap: 6px; }
.lab { font-size: 11px; font-weight: 700; background: #e4e7ec; color: #344054; border-radius: 3px; padding: 0 5px; }
.name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.x { border: none; background: none; font-size: 16px; line-height: 1; color: var(--muted); padding: 0 2px; }
.shift { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin-top: 4px; font-size: 12px; }
.shift label { display: flex; gap: 4px; align-items: center; }
.shift input { width: 64px; font: inherit; padding: 2px 4px; border: 1px solid var(--border); border-radius: 4px; }
.shift button { font-size: 11px; padding: 2px 6px; border: 1px solid var(--border); background: var(--bg); border-radius: 4px; }
.none { color: var(--muted); font-size: 12px; cursor: default; border: none; }
</style>
