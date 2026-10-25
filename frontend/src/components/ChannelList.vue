<script setup lang="ts">
import { computed, ref } from 'vue'
import type { ChannelOut } from '../api/client'

const props = defineProps<{ fileId: string; channels: ChannelOut[]; complete: boolean; used: Set<string>; charts: string[]; disabled: boolean }>()
const emit = defineEmits<{ add: [channel: string, chartIndex: number] }>()

const query = ref('')
const visible = computed(() => {
  const q = query.value.trim().toLowerCase()
  return q ? props.channels.filter((c) => c.name.toLowerCase().includes(q)) : props.channels
})

function onDragStart(e: DragEvent, name: string) {
  if (props.disabled) return e.preventDefault()
  e.dataTransfer?.setData('text/channel', name)
  e.dataTransfer?.setData('text/file', props.fileId)
  if (e.dataTransfer) e.dataTransfer.effectAllowed = 'copy'
}

function onMenu(e: Event, name: string) {
  const sel = e.target as HTMLSelectElement
  if (sel.value !== '') emit('add', name, Number(sel.value))
  sel.value = ''
}

const hz = (v: number | null | undefined) => (v == null ? '' : v >= 10 ? v.toFixed(0) : String(+v.toFixed(2)))
</script>

<template>
  <div class="wrap">
    <input v-model="query" type="search" placeholder="Search channels…" />
    <div class="count">
      {{ visible.length }} / {{ channels.length }} channels
      <span v-if="!complete && channels.length"> · catalogue pending</span>
    </div>
    <ul class="list">
      <li v-for="c in visible" :key="c.name" :class="{ off: disabled }" :draggable="!disabled" @dragstart="onDragStart($event, c.name)">
        <span class="dot" :class="{ on: used.has(c.name) }" :title="used.has(c.name) ? 'Used in this tab' : ''"></span>
        <span class="name" :title="c.name">{{ c.name }}</span>
        <span v-if="c.event" class="badge event">event</span>
        <span v-if="c.native_hz" class="meta" :title="`native ${hz(c.native_hz)} Hz, assigned ${hz(c.assigned_hz)} Hz`">
          {{ hz(c.native_hz) }}→{{ hz(c.assigned_hz) }}
        </span>
        <select class="add" :disabled="disabled" title="Add to graph" @change="onMenu($event, c.name)">
          <option value="">+</option>
          <option v-for="(label, i) in charts" :key="i" :value="i">{{ label }}</option>
        </select>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.wrap { display: flex; flex-direction: column; gap: 6px; min-height: 0; flex: 1; }
.count { font-size: 12px; color: var(--muted); }
.list { list-style: none; margin: 0; padding: 0; overflow: auto; flex: 1; border: 1px solid var(--border); border-radius: 6px; background: var(--bg); }
li { display: flex; align-items: center; gap: 6px; padding: 3px 8px; cursor: grab; }
li:hover { background: var(--panel); }
li.off { opacity: 0.55; cursor: default; }
.dot { width: 7px; height: 7px; border-radius: 50%; background: transparent; flex: none; }
.dot.on { background: var(--accent); }
.name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.meta { font-size: 11px; color: var(--muted); white-space: nowrap; }
.add { width: 38px; font-size: 12px; border: 1px solid var(--border); border-radius: 4px; background: var(--bg); }
</style>
