<script setup lang="ts">
import type { Formula } from '../composables/formula'
import type { FormulaResult } from '../composables/formulaEval'

defineProps<{ formulas: Formula[]; results: Record<string, FormulaResult>; busy: Record<string, boolean>; charts: string[]; used: Set<string>; canAdd: boolean }>()
const emit = defineEmits<{ create: []; edit: [id: string]; remove: [id: string]; add: [id: string, chartIndex: number] }>()

function onDragStart(e: DragEvent, id: string) {
  e.dataTransfer?.setData('text/channel', id)
  e.dataTransfer?.setData('text/file', '~calc')
  if (e.dataTransfer) e.dataTransfer.effectAllowed = 'copy'
}
function onMenu(e: Event, id: string) {
  const s = e.target as HTMLSelectElement
  if (s.value !== '') emit('add', id, Number(s.value))
  s.value = ''
}
</script>

<template>
  <div>
    <header class="row">
      <h2>Calculated channels</h2>
      <button :disabled="!canAdd" title="Create a formula from channels, constants and operators" @click="emit('create')">New</button>
    </header>
    <ul class="fl">
      <li v-for="f in formulas" :key="f.id" :draggable="!!results[f.id]?.output" @dragstart="onDragStart($event, f.id)">
        <span class="dot" :class="{ on: used.has(f.id) }"></span>
        <span class="n" :title="results[f.id]?.error ?? f.name">ƒ {{ f.name }}</span>
        <span v-if="busy[f.id]" class="s">…</span>
        <span v-else-if="results[f.id]?.error" class="s e" :title="results[f.id]?.error">⚠</span>
        <button class="b" title="Edit" @click="emit('edit', f.id)">Edit</button>
        <select class="add" :disabled="!results[f.id]?.output" title="Add to graph" @change="onMenu($event, f.id)">
          <option value="">+</option>
          <option v-for="(label, i) in charts" :key="i" :value="i">{{ label }}</option>
        </select>
        <button class="b" title="Delete this calculated channel" @click="emit('remove', f.id)">×</button>
      </li>
      <li v-if="!formulas.length" class="none">None yet. Create one, or load a template.</li>
    </ul>
  </div>
</template>

<style scoped>
h2 { margin: 0 0 8px; font-size: 13px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); }
.row { display: flex; justify-content: space-between; align-items: center; }
.fl { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
li { display: flex; align-items: center; gap: 6px; border: 1px solid var(--border); border-radius: 6px; background: var(--bg); padding: 3px 6px; cursor: grab; }
li.none { color: var(--muted); font-size: 12px; border: none; background: none; cursor: default; }
.dot { width: 7px; height: 7px; border-radius: 50%; background: transparent; flex: none; }
.dot.on { background: var(--accent); }
.n { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.s { font-size: 12px; color: var(--muted); }
.s.e { color: var(--danger); }
.b { border: none; background: none; color: var(--accent); font-size: 12px; padding: 0 2px; }
.add { width: 38px; font-size: 12px; border: 1px solid var(--border); border-radius: 4px; background: var(--bg); }
</style>
