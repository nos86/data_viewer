<script setup lang="ts">
import { reactive } from 'vue'

const props = defineProps<{ tabName: string; seriesCount: number; windowText: string; decimal: string; busy: boolean; error: string | null }>()
const emit = defineEmits<{ confirm: [o: { format: 'csv' | 'parquet'; spreadsheet: boolean; filename: string }]; cancel: [] }>()

const o = reactive({ format: 'csv' as 'csv' | 'parquet', spreadsheet: props.decimal === ',', filename: `${props.tabName} data` })
</script>

<template>
  <div class="overlay" @keydown.stop @keyup.stop @keydown.esc="emit('cancel')">
    <div class="dlg" role="dialog" aria-label="Export data">
      <h3>Export data</h3>
      <p class="muted">
        What is on screen in <strong>{{ tabName }}</strong>: {{ seriesCount }} channel{{ seriesCount === 1 ? '' : 's' }}, {{ windowText }}.
        One row per grid point of each channel, in the long format <code>time_s, channel, value</code>; every channel keeps its own grid and
        missing values produce no row (nothing is filled).
      </p>
      <div class="row">
        <label><input v-model="o.format" type="radio" value="csv" /> CSV</label>
        <label><input v-model="o.format" type="radio" value="parquet" /> Parquet</label>
      </div>
      <label v-if="o.format === 'csv'" class="chk">
        <input v-model="o.spreadsheet" type="checkbox" />
        Spreadsheet friendly: decimal “{{ decimal }}”<template v-if="decimal === ','"> and “;” as separator</template> (your language)
      </label>
      <label class="name">File name <input v-model="o.filename" type="text" /></label>
      <p v-if="error" class="err">{{ error }}</p>
      <footer>
        <button :disabled="busy" @click="emit('cancel')">Cancel</button>
        <button class="primary" :disabled="busy || !seriesCount" @click="emit('confirm', { ...o })">{{ busy ? 'Exporting…' : 'Export' }}</button>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.overlay { position: fixed; inset: 0; background: rgba(16, 24, 40, 0.45); z-index: 60; display: grid; place-items: center; padding: 24px; }
.dlg { background: var(--bg); border-radius: 10px; width: min(520px, 100%); padding: 16px 20px; border: 1px solid var(--border); display: flex; flex-direction: column; gap: 10px; }
h3 { margin: 0; }
.muted { color: var(--muted); font-size: 12px; margin: 0; }
code { background: var(--panel); padding: 0 4px; border-radius: 3px; }
.row { display: flex; gap: 18px; }
.chk { font-size: 13px; }
.name { display: flex; gap: 8px; align-items: center; }
.name input { flex: 1; font: inherit; padding: 4px 8px; border: 1px solid var(--border); border-radius: 6px; }
.err { color: var(--danger); font-size: 12px; margin: 0; }
footer { display: flex; justify-content: flex-end; gap: 8px; }
.primary { background: var(--accent); color: #fff; border: none; border-radius: 6px; padding: 5px 14px; }
.primary:disabled { opacity: 0.5; }
</style>
