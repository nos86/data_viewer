<script setup lang="ts">
import { computed } from 'vue'
import type { ChannelOut } from '../api/client'
import { isCalc, lineName, addAxis, removeAxis, removeChart, removeLine, setBand, setDivisions, type Chart, type Dash, type Mode, type Tab } from '../composables/layout'

export type Selection = { chartId: string; kind: 'chart' | 'axis' | 'line'; id: string } | null

export interface Catalogue {
  fileId: string
  label: string
  name: string
  channels: ChannelOut[]
}

const props = defineProps<{
  tab: Tab
  selection: Selection
  channels: Map<string, ChannelOut> // key: fileId|channel
  catalogue: Catalogue[]
  multi: boolean // several measurements open: show labels
}>()
const emit = defineEmits<{ select: [s: Selection]; removeTab: [] }>()

const chart = computed<Chart | undefined>(() => props.tab.charts.find((c) => c.id === props.selection?.chartId))
const axis = computed(() => (props.selection?.kind === 'axis' ? chart.value?.axes.find((a) => a.id === props.selection!.id) : undefined))
const line = computed(() => (props.selection?.kind === 'line' ? chart.value?.lines.find((l) => l.id === props.selection!.id) : undefined))
const modes = computed<Mode[]>(() => {
  const ch = line.value && props.channels.get(`${line.value.fileId}|${line.value.channel}`)
  return (ch?.modes_allowed as Mode[] | undefined) ?? ['step', 'linear']
})
const num = (e: Event) => Number((e.target as HTMLInputElement).value)
const optNum = (e: Event) => {
  const v = (e.target as HTMLInputElement).value.trim()
  return v === '' || isNaN(Number(v)) ? null : Number(v)
}

function setLineAxis(e: Event) {
  const v = (e.target as HTMLSelectElement).value
  if (!line.value || !chart.value) return
  line.value.axisId = v === '__new' ? addAxis(chart.value).id : v
}
function dropAxis() {
  if (!axis.value || !chart.value) return
  removeAxis(chart.value, axis.value.id)
  emit('select', { chartId: chart.value.id, kind: 'chart', id: chart.value.id })
}
function dropLine() {
  if (!line.value || !chart.value) return
  removeLine(chart.value, line.value.id)
  emit('select', { chartId: chart.value.id, kind: 'chart', id: chart.value.id })
}
const labelOf = (fileId: string) => props.catalogue.find((c) => c.fileId === fileId)?.label ?? '?'
const xChannels = computed(() => props.catalogue.find((c) => c.fileId === chart.value?.xy?.x?.fileId)?.channels ?? [])
function setXFile(e: Event) {
  const fileId = (e.target as HTMLSelectElement).value
  const first = props.catalogue.find((c) => c.fileId === fileId)?.channels[0]
  if (chart.value?.xy && first) chart.value.xy.x = { fileId, channel: first.name, mode: first.suggested_mode === 'step' ? 'step' : 'linear' }
}
function setXChannel(e: Event) {
  const x = chart.value?.xy?.x
  const name = (e.target as HTMLSelectElement).value
  if (!x) return
  const info = props.channels.get(`${x.fileId}|${name}`)
  x.channel = name
  x.mode = info?.suggested_mode === 'step' ? 'step' : 'linear'
}
function initX() {
  const c = props.catalogue.find((k) => k.channels.length)
  if (chart.value?.xy && c) chart.value.xy.x = { fileId: c.fileId, channel: c.channels[0].name, mode: 'linear' }
}
const xModes = computed<Mode[]>(() => {
  const x = chart.value?.xy?.x
  const m = x && props.channels.get(`${x.fileId}|${x.channel}`)?.modes_allowed
  return (m as Mode[] | undefined) ?? ['step', 'linear']
})
const yAxis = computed(() => chart.value?.axes[0])

function dropChart() {
  if (!chart.value) return
  removeChart(props.tab, chart.value.id)
  emit('select', null)
}
</script>

<template>
  <div class="insp">
    <template v-if="line && chart">
      <h3>{{ chart?.kind === 'xy' ? 'Y line' : 'Line' }} · {{ lineName(line) }}<span v-if="isCalc(line)"> (calculated)</span><span v-else-if="multi"> ({{ labelOf(line.fileId) }})</span></h3>
      <label>Mode
        <select v-model="line.mode">
          <option v-for="m in modes" :key="m" :value="m">{{ m }}</option>
        </select>
      </label>
      <label v-if="chart.kind === 'time'">Axis
        <select :value="line.axisId" @change="setLineAxis">
          <option v-for="(a, i) in chart.axes" :key="a.id" :value="a.id">Y{{ i + 1 }} ({{ a.side }})</option>
          <option value="__new">New axis…</option>
        </select>
      </label>
      <label>Colour <input v-model="line.color" type="color" /></label>
      <label>Width <input v-model.number="line.width" type="number" min="0.5" max="6" step="0.5" /></label>
      <label>Style
        <select v-model="line.dash">
          <option v-for="d in (['solid', 'dashed', 'dotted'] as Dash[])" :key="d" :value="d">{{ d }}</option>
        </select>
      </label>
      <label class="chk"><input v-model="line.marker" type="checkbox" /> Markers on all points</label>
      <button class="danger" @click="dropLine">Remove line</button>
    </template>

    <template v-else-if="axis && chart">
      <h3>Axis Y{{ chart.axes.indexOf(axis) + 1 }}</h3>
      <label>Side
        <select v-model="axis.side"><option value="left">left</option><option value="right">right</option></select>
      </label>
      <div class="two">
        <label>Band from <input type="number" min="0" :max="chart.divisions - 1" :value="axis.band[0]" @change="setBand(chart, axis.id, num($event), axis.band[1])" /></label>
        <label>to <input type="number" :min="1" :max="chart.divisions" :value="axis.band[1]" @change="setBand(chart, axis.id, axis.band[0], num($event))" /></label>
      </div>
      <small>Divisions of {{ chart.divisions }}, counted from the bottom.</small>
      <div class="two">
        <label>Min <input type="number" step="any" placeholder="auto" :value="axis.min ?? ''" @change="axis.min = optNum($event)" /></label>
        <label>Max <input type="number" step="any" placeholder="auto" :value="axis.max ?? ''" @change="axis.max = optNum($event)" /></label>
      </div>
      <button class="danger" @click="dropAxis">Remove axis</button>
    </template>

    <template v-else-if="chart && chart.kind === 'xy' && chart.xy">
      <h3>XY graph {{ tab.charts.indexOf(chart) + 1 }}</h3>
      <template v-if="chart.xy.x">
        <label v-if="multi">X from
          <select :value="chart.xy.x.fileId" @change="setXFile">
            <option v-for="c in catalogue" :key="c.fileId" :value="c.fileId">{{ c.label }} · {{ c.name }}</option>
          </select>
        </label>
        <label>X channel
          <select :value="chart.xy.x.channel" @change="setXChannel">
            <option v-for="c in xChannels" :key="c.name" :value="c.name">{{ c.name }}</option>
          </select>
        </label>
        <label>X mode
          <select v-model="chart.xy.x.mode"><option v-for="m in xModes" :key="m" :value="m">{{ m }}</option></select>
        </label>
      </template>
      <button v-else @click="initX">Choose X channel</button>
      <div class="two">
        <label>X min <input type="number" step="any" placeholder="auto" :value="chart.xy.xMin ?? ''" @change="chart.xy.xMin = optNum($event)" /></label>
        <label>X max <input type="number" step="any" placeholder="auto" :value="chart.xy.xMax ?? ''" @change="chart.xy.xMax = optNum($event)" /></label>
      </div>
      <div v-if="yAxis" class="two">
        <label>Y min <input type="number" step="any" placeholder="auto" :value="yAxis.min ?? ''" @change="yAxis.min = optNum($event)" /></label>
        <label>Y max <input type="number" step="any" placeholder="auto" :value="yAxis.max ?? ''" @change="yAxis.max = optNum($event)" /></label>
      </div>
      <label>Divisions
        <input type="number" min="2" max="40" :value="chart.divisions" @change="setDivisions(chart, num($event))" />
      </label>
      <small>Drop channels on the graph to add Y lines. Points are the times both channels share; holes are skipped.</small>
      <button class="danger" @click="dropChart">Remove graph</button>
    </template>

    <template v-else-if="chart">
      <h3>Graph {{ tab.charts.indexOf(chart) + 1 }}</h3>
      <label>Vertical divisions
        <input type="number" min="2" max="40" :value="chart.divisions" @change="setDivisions(chart, num($event))" />
      </label>
      <button @click="addAxis(chart)">Add axis</button>
      <button class="danger" @click="dropChart">Remove graph</button>
    </template>

    <template v-else>
      <h3>Tab</h3>
      <label>Name <input v-model="tab.name" type="text" /></label>
      <button class="danger" @click="emit('removeTab')">Remove tab</button>
      <small>Click a graph, an axis or a legend entry to edit it. Drag channels from the list onto a graph or axis.</small>
    </template>
  </div>
</template>

<style scoped>
.insp { display: flex; flex-direction: column; gap: 8px; font-size: 13px; }
h3 { margin: 0; font-size: 13px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); }
label { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
label.chk { justify-content: flex-start; }
input[type='number'], input[type='text'], select { width: 120px; font: inherit; padding: 3px 6px; border: 1px solid var(--border); border-radius: 4px; }
input[type='color'] { width: 48px; height: 24px; padding: 0; border: 1px solid var(--border); }
.two { display: flex; gap: 8px; }
.two label { flex: 1; flex-direction: column; align-items: flex-start; gap: 2px; }
.two input { width: 100%; }
button { padding: 4px 8px; border: 1px solid var(--border); background: var(--bg); border-radius: 4px; }
button.danger { color: var(--danger); }
small { color: var(--muted); }
</style>
