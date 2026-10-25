<script setup lang="ts">
import { computed } from 'vue'
import type { ChannelOut, SeriesData } from '../api/client'
import { cursorColor, kToSeconds, type Cursor } from '../composables/cursors'
import type { Line } from '../composables/layout'
import { formatInstant, instantOfStep, localize } from '../composables/locale'
import type { Placement } from '../composables/readout'
import { cursorTable } from '../composables/tables'

const props = defineProps<{
  lines: Line[] // lines of the current tab
  series: Map<string, SeriesData>
  channels: Map<string, ChannelOut> // key: fileId|channel
  cursors: Cursor[]
  activeId: string | null
  stepBaseUs: number
  placements: Record<string, Placement>
  labels: Record<string, string> | null
  t0Us: number | null // absolute start of the primary measurement, for the clock times in the header (RF-38)
}>()
const emit = defineEmits<{ activate: [id: string] }>()

const table = computed(() => cursorTable(props.lines, props.cursors, { series: props.series, channels: props.channels, placements: props.placements, labels: props.labels, stepBaseUs: props.stepBaseUs }))
const clock = (k: number) => (props.t0Us == null ? '' : formatInstant(instantOfStep(props.t0Us, k, props.stepBaseUs)))
const title = (k: number) =>
  `${localize(kToSeconds(k, props.stepBaseUs).toFixed(1))} s` + (props.t0Us == null ? '' : ` · ${formatInstant(instantOfStep(props.t0Us, k, props.stepBaseUs), { date: true })}`)
</script>

<template>
  <div class="wrap">
    <table v-if="cursors.length && table.rows.length">
      <thead>
        <tr>
          <th class="ch">Channel</th>
          <th v-for="col in table.columns" :key="col.cursor.id + col.at" :class="{ act: col.cursor.id === activeId }" :title="title(col.cursor[col.at])" @click="emit('activate', col.cursor.id)">
            <span class="dot" :style="{ background: cursorColor(col.cursor) }"></span>{{ col.label }}
          </th>
        </tr>
        <tr v-if="t0Us != null" class="clock">
          <th class="ch">Time</th>
          <th v-for="col in table.columns" :key="col.cursor.id + col.at" :class="{ act: col.cursor.id === activeId }">{{ clock(col.cursor[col.at]) }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="r in table.rows" :key="r.line.id">
          <td class="ch"><span class="sw" :style="{ background: r.line.color }"></span><i v-if="r.tag" class="lab">{{ r.tag }}</i>{{ r.name }}<small v-if="r.line.mode !== 'linear'"> · {{ r.line.mode }}</small></td>
          <td v-for="(cell, i) in r.cells" :key="i" :class="{ off: cell.off, act: table.columns[i].cursor.id === activeId }" :title="cell.off && cell.text !== 'out of measure' ? 'Cursor between grid points of this channel: value of the previous point' : ''">
            {{ cell.text }}
          </td>
        </tr>
      </tbody>
    </table>
    <p v-else class="none">{{ cursors.length ? 'No channels in this tab.' : 'No cursors yet. Use the Cursor / Band buttons, N / B, or Alt+click on a graph.' }}</p>
  </div>
</template>

<style scoped>
.wrap { overflow: auto; }
table { border-collapse: collapse; font-size: 12px; font-variant-numeric: tabular-nums; }
th, td { padding: 2px 10px; text-align: right; border-bottom: 1px solid var(--border); white-space: nowrap; }
th { cursor: pointer; background: var(--panel); position: sticky; top: 0; }
tr.clock th { top: 21px; font-weight: 400; color: var(--muted); cursor: default; }
th.act, td.act { background: #eef6ff; }
.ch { text-align: left; position: sticky; left: 0; background: var(--bg); }
th.ch { background: var(--panel); cursor: default; }
td.off { font-style: italic; color: var(--muted); }
.dot, .sw { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
.sw { border-radius: 2px; }
.lab { font-style: normal; font-size: 10px; font-weight: 700; background: #e4e7ec; color: #344054; border-radius: 3px; padding: 0 4px; margin-right: 4px; }
small { color: var(--muted); }
.none { margin: 6px 0; color: var(--muted); font-size: 12px; }
</style>
