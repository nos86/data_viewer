<script setup lang="ts">
import type { FileOut } from '../api/client'

defineProps<{ files: FileOut[]; openIds: Record<string, string>; filtered?: boolean; capped?: boolean }>() // id -> label of the open measurements
defineEmits<{ open: [file: FileOut] }>()
</script>

<template>
  <ul class="files">
    <li v-for="f in files" :key="f.id">
      <button :class="{ active: !!openIds[f.id] }" @click="$emit('open', f)">
        <span class="name">{{ f.name }}<small v-if="f.vin || f.start" class="sub">{{ [f.vin, f.configuration_id, f.start?.slice(0, 16).replace('T', ' ')].filter(Boolean).join(' · ') }}</small></span>
        <span v-if="openIds[f.id]" class="lab">{{ openIds[f.id] }}</span>
        <span class="badge" :class="f.state">{{ f.state.replace('_', ' ') }}</span>
      </button>
    </li>
    <li v-if="capped" class="none">Showing the most recent measurements only: narrow the list with the selector.</li>
    <li v-if="!files.length" class="none">{{ filtered ? 'No measurement matches the selection.' : 'No measurements found.' }}</li>
  </ul>
</template>

<style scoped>
.files { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
button { width: 100%; display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; background: var(--bg); color: var(--text); text-align: left; }
button.active { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent) inset; }
.name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: flex; flex-direction: column; }
.sub { color: var(--muted); font-size: 11px; overflow: hidden; text-overflow: ellipsis; }
.lab { font-size: 11px; font-weight: 700; background: #e4e7ec; color: #344054; border-radius: 3px; padding: 0 5px; }
.none { color: var(--muted); font-size: 12px; }
</style>
