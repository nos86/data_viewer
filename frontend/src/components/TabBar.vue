<script setup lang="ts">
import type { Tab } from '../composables/layout'

defineProps<{ tabs: Tab[]; activeId: string }>()
defineEmits<{ select: [id: string]; add: [] }>()
</script>

<template>
  <div class="tabs" role="tablist">
    <button v-for="t in tabs" :key="t.id" role="tab" :class="{ active: t.id === activeId }" @click="$emit('select', t.id)">{{ t.name }}</button>
    <button class="add" title="New tab" @click="$emit('add')">+</button>
  </div>
</template>

<style scoped>
.tabs { display: flex; gap: 2px; border-bottom: 1px solid var(--border); }
button { border: 1px solid transparent; border-bottom: none; background: transparent; padding: 5px 12px; border-radius: 6px 6px 0 0; color: var(--muted); }
button.active { background: var(--bg); border-color: var(--border); color: var(--text); margin-bottom: -1px; }
.add { font-weight: 700; }
</style>
