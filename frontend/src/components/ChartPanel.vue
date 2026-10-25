<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import uPlot from 'uplot'
import type { SeriesData } from '../api/client'
import { FONT, laneOffset, PAD, TICK_LEN, type ChartMetrics } from '../composables/axes'
import { gridSeconds } from '../composables/grid'
import type { Cursor } from '../composables/cursors'
import CursorLayer from './CursorLayer.vue'
import { canvasToBlob, composeSnapshot, downloadBlob, safeFileName, type LegendItem } from '../composables/chartImage'
import { localeNumber } from '../composables/locale'
import { lineName, lineTag, seriesKey, type Chart, type Dash, type Line } from '../composables/layout'

const props = defineProps<{
  chart: Chart
  metrics: ChartMetrics
  margin: { left: number; right: number }
  series: Map<string, SeriesData>
  xWindow: [number, number] | null
  extent: [number, number] // shared time axis of all open measurements, seconds (may start below 0)
  shifts: Record<string, number> // measurement id -> shift in steps (RF-03)
  labels: Record<string, string> | null // measurement id -> A/B/..; null when a single measurement is open (RF-17)
  stepBaseUs: number
  markerLimit: number
  acceptedMarkers: Set<string>
  selectedId: string | null
  index: number
  cursors: Cursor[]
  activeCursorId: string | null
  kb: { min: number; max: number }
  readings: Record<string, { text: string; off: boolean }> // value of the active cursor per line
  legendWidth: number // fixed item width, set at configuration time (RI-31)
  tabName: string
}>()
const emit = defineEmits<{
  xchange: [w: [number, number] | null]
  selectLine: [id: string]
  selectAxis: [id: string]
  selectChart: []
  dropChannel: [channel: string, axisId: string | null, fileId: string | null]
  markerAccept: [lineId: string]
  markerReject: [lineId: string]
  cursorActivate: [id: string]
  cursorDrag: [id: string, part: 'single' | 'start' | 'end' | 'whole', k: number]
  cursorCreate: [kind: 'single' | 'band', k: number, k2: number]
  cursorGoTo: [id: string]
}>()

const body = ref<HTMLDivElement>()
const host = ref<HTMLDivElement>()
const overlay = ref<HTMLCanvasElement>()
const geo = ref<Geo | null>(null)
const view = ref<[number, number]>(props.xWindow ?? props.extent)
const warnLines = ref<{ id: string; name: string; points: number }[]>([])
let u: uPlot | null = null
let syncing = false
let ro: ResizeObserver | null = null
let rafId = 0

const DASH: Record<Dash, number[]> = { solid: [], dashed: [9, 5], dotted: [2, 4] }
const dashCss: Record<Dash, string> = { solid: '', dashed: '6 3', dotted: '1.5 3' }

const lineKey = (l: Line) => seriesKey(l.fileId, l.channel, l.mode)
const shiftOf = (l: Line) => props.shifts[l.fileId] ?? 0
const dataOf = (l: Line) => props.series.get(lineKey(l))
const axisColor = (axisId: string) => {
  const cols = new Set(props.chart.lines.filter((l) => l.axisId === axisId).map((l) => l.color))
  return cols.size === 1 ? [...cols][0] : '#555'
}

function xWin(): [number, number] {
  return props.xWindow ?? props.extent
}

// ---- uPlot --------------------------------------------------------------------------------------

function visiblePoints(l: Line, d: SeriesData, win: [number, number]): number {
  const step = (d.n * props.stepBaseUs) / 1e6
  const t = (j: number) => ((j * d.n + shiftOf(l)) * props.stepBaseUs) / 1e6
  const lo = Math.max(win[0], t(d.jStart))
  const hi = Math.min(win[1], t(d.jStart + d.values.length - 1))
  return Math.max(0, Math.floor((hi - lo) / step) + 1)
}

/** Markers on all points (RF-16); above the limit they wait for the user's decision. */
function updateMarkers() {
  if (!u) return
  const win = xWin()
  const warn: typeof warnLines.value = []
  let changed = false
  props.chart.lines.forEach((l) => {
    const si = built.indexOf(l.id)
    if (si < 0) return
    const d = dataOf(l)!
    let show = false
    if (l.marker) {
      const pts = visiblePoints(l, d, [u!.scales.x.min ?? win[0], u!.scales.x.max ?? win[1]])
      if (pts > props.markerLimit && !props.acceptedMarkers.has(l.id)) warn.push({ id: l.id, name: lineName(l), points: pts })
      else show = true
    }
    if (markerShown[l.id] !== show) {
      markerShown[l.id] = show
      // uPlot turns points.show into a function at init; it must stay one
      ;(u!.series[si + 1].points as { show: unknown }).show = () => show
      changed = true
    }
  })
  warnLines.value = warn
  if (changed) u.redraw(false)
}

let built: string[] = [] // line ids in uPlot series order
let markerShown: Record<string, boolean> = {}

function build() {
  u?.destroy()
  u = null
  built = []
  markerShown = {}
  const el = host.value
  if (!el) return
  const lines = props.chart.lines.filter((l) => dataOf(l))
  drawOverlay()
  if (!lines.length) return

  const scales: Record<string, uPlot.Scale> = { x: { time: false } }
  for (const ax of props.chart.axes) {
    scales[ax.id] = { range: () => props.metrics.ticks.get(ax.id)?.scale ?? [0, 1] }
  }
  const series = [{}] as uPlot.Series[]
  const data: unknown[] = [null]
  for (const l of lines) {
    const d = dataOf(l)!
    const x = gridSeconds(d.jStart, d.n, d.values.length, props.stepBaseUs, shiftOf(l))
    const y: (number | null)[] = new Array(d.values.length)
    for (let i = 0; i < y.length; i++) y[i] = Number.isNaN(d.values[i]) ? null : d.values[i]
    data.push([x, y])
    built.push(l.id)
    series.push({
      label: lineName(l),
      stroke: l.color,
      width: l.width,
      dash: DASH[l.dash],
      spanGaps: false,
      scale: l.axisId,
      paths: l.mode === 'step' ? uPlot.paths.stepped?.({ align: 1 }) : uPlot.paths.linear?.(),
      points: { show: false, size: 5, fill: l.color, stroke: l.color, width: 0 },
      facets: [
        { scale: 'x', auto: true },
        { scale: l.axisId, auto: false },
      ],
    } as uPlot.Series)
  }

  const w = el.clientWidth
  const h = el.clientHeight
  u = new uPlot(
    {
      mode: 2,
      width: w,
      height: h,
      padding: [8, PAD, 0, PAD],
      legend: { show: false },
      scales,
      axes: [{ scale: 'x', size: 26, font: FONT, stroke: '#667085', grid: { show: false }, ticks: { size: 4 } }],
      series,
      cursor: { drag: { x: true, y: false, setScale: true }, points: { show: false } },
      plugins: [interactions()],
      hooks: {
        drawClear: [drawGrid],
        setScale: [
          (uu, key) => {
            if (key !== 'x' || syncing) return
            const min = uu.scales.x.min
            const max = uu.scales.x.max
            if (min == null || max == null) return
            view.value = [min, max]
            emit('xchange', [min, max])
            scheduleMarkers()
          },
        ],
        setSize: [() => drawOverlay()],
      },
    } as uPlot.Options,
    data as uPlot.AlignedData,
    el,
  )
  syncing = true
  const win = xWin()
  u.setScale('x', { min: win[0], max: win[1] })
  syncing = false
  view.value = win
  updateMarkers()
  drawOverlay()
}

const scheduleMarkers = () => {
  cancelAnimationFrame(rafId)
  rafId = requestAnimationFrame(updateMarkers)
}

function drawGrid(uu: uPlot) {
  const D = props.chart.divisions
  const { left, top, width, height } = uu.bbox
  const ctx = uu.ctx
  const dpr = devicePixelRatio
  ctx.save()
  ctx.strokeStyle = '#e3e6ea'
  ctx.lineWidth = dpr
  ctx.beginPath()
  for (let i = 0; i <= D; i++) {
    const y = Math.round(top + height * (1 - i / D)) + 0.5 * dpr
    ctx.moveTo(left, y)
    ctx.lineTo(left + width, y)
  }
  ctx.stroke()
  ctx.restore()
}

function interactions(): uPlot.Plugin {
  const clamp = (min: number, max: number): [number, number] => {
    const [e0, e1] = props.extent
    const span = Math.min(max - min, e1 - e0)
    const nmin = Math.max(e0, Math.min(min, e1 - span))
    return [nmin, nmin + span]
  }
  return {
    hooks: {
      ready: (uu) => {
        const over = uu.over
        over.addEventListener(
          'wheel',
          (e) => {
            e.preventDefault()
            const xv = uu.posToVal(e.clientX - over.getBoundingClientRect().left, 'x')
            const { min, max } = uu.scales.x
            if (min == null || max == null) return
            const f = e.deltaY < 0 ? 0.8 : 1.25
            const [a, b] = clamp(xv - (xv - min) * f, xv + (max - xv) * f)
            uu.setScale('x', { min: a, max: b })
          },
          { passive: false },
        )
        over.addEventListener(
          'mousedown',
          (e) => {
            if (!(e.button === 1 || (e.button === 0 && e.shiftKey))) return
            e.preventDefault()
            e.stopImmediatePropagation()
            const x0 = e.clientX
            const { min, max } = uu.scales.x
            if (min == null || max == null) return
            const perPx = (max - min) / (uu.bbox.width / devicePixelRatio)
            const move = (ev: MouseEvent) => {
              const d = (x0 - ev.clientX) * perPx
              const [a, b] = clamp(min + d, max + d)
              uu.setScale('x', { min: a, max: b })
            }
            const up = () => {
              window.removeEventListener('mousemove', move)
              window.removeEventListener('mouseup', up)
            }
            window.addEventListener('mousemove', move)
            window.addEventListener('mouseup', up)
          },
          true,
        )
        over.addEventListener('dblclick', () => emit('xchange', null))
      },
    },
  }
}

// ---- axis overlay (ticks and labels only inside each axis band) -------------------------------------

interface Geo {
  top: number
  height: number
  left: number
  right: number
}

function plotGeo(): Geo | null {
  const b = body.value
  if (!b) return null
  const dpr = devicePixelRatio
  if (u) return { top: u.bbox.top / dpr, height: u.bbox.height / dpr, left: props.margin.left - PAD + u.bbox.left / dpr, right: props.margin.left - PAD + (u.bbox.left + u.bbox.width) / dpr }
  // no data yet: same geometry as the uPlot canvas would have
  const h = b.clientHeight
  return { top: 8, height: Math.max(h - 8 - 26, 10), left: props.margin.left, right: b.clientWidth - props.margin.right }
}

const yOf = (g: Geo, div: number) => g.top + g.height * (1 - div / props.chart.divisions)

function drawOverlay() {
  const cv = overlay.value
  const b = body.value
  if (!cv || !b) return
  const dpr = devicePixelRatio
  const W = b.clientWidth
  const H = b.clientHeight
  if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) {
    cv.width = Math.round(W * dpr)
    cv.height = Math.round(H * dpr)
    cv.style.width = W + 'px'
    cv.style.height = H + 'px'
  }
  const ctx = cv.getContext('2d')!
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, W, H)
  const g = plotGeo()
  if (!g) return
  geo.value = g
  ctx.font = FONT
  ctx.textBaseline = 'middle'
  if (!u) {
    // keep the grid visible while no series is plotted
    ctx.strokeStyle = '#e3e6ea'
    ctx.beginPath()
    for (let i = 0; i <= props.chart.divisions; i++) {
      ctx.moveTo(g.left, Math.round(yOf(g, i)) + 0.5)
      ctx.lineTo(g.right, Math.round(yOf(g, i)) + 0.5)
    }
    ctx.stroke()
  }
  for (const ax of props.chart.axes) {
    const pl = props.metrics.placement.get(ax.id)
    const tk = props.metrics.ticks.get(ax.id)
    if (!pl || !tk) continue
    const left = ax.side === 'left'
    const off = laneOffset(props.metrics.laneWidth[ax.side], pl.lane)
    const xLine = left ? g.left - off : g.right + off
    const dir = left ? -1 : 1
    const col = axisColor(ax.id)
    const sel = props.selectedId === ax.id
    ctx.strokeStyle = col
    ctx.fillStyle = col
    ctx.lineWidth = sel ? 2.5 : 1
    ctx.beginPath()
    ctx.moveTo(xLine + 0.5, yOf(g, ax.band[0]))
    ctx.lineTo(xLine + 0.5, yOf(g, ax.band[1]))
    tk.divisions.forEach((d, i) => {
      const y = Math.round(yOf(g, d)) + 0.5
      ctx.moveTo(xLine + 0.5, y)
      ctx.lineTo(xLine + 0.5 + dir * TICK_LEN, y)
    })
    ctx.stroke()
    ctx.textAlign = left ? 'right' : 'left'
    tk.labels.forEach((t, i) => {
      if (pl.omitTop && i === tk.labels.length - 1) return
      const d = tk.divisions[i]
      let y = yOf(g, d)
      if (i === 0) y -= 3 // keep the end labels inside the band
      else if (i === tk.labels.length - 1) y += 3
      ctx.fillText(t, xLine + dir * (TICK_LEN + 3), y)
    })
  }
}

function axisAt(x: number, y: number): string | null {
  const g = plotGeo()
  if (!g) return null
  for (const ax of props.chart.axes) {
    const pl = props.metrics.placement.get(ax.id)
    if (!pl) continue
    const w = props.metrics.laneWidth[ax.side][pl.lane] ?? 0
    const off = laneOffset(props.metrics.laneWidth[ax.side], pl.lane)
    const x0 = ax.side === 'left' ? g.left - off - w : g.right + off
    if (x >= x0 && x <= x0 + w && y <= yOf(g, ax.band[0]) + 4 && y >= yOf(g, ax.band[1]) - 4) return ax.id
  }
  return null
}

function localXY(e: MouseEvent) {
  const r = body.value!.getBoundingClientRect()
  return { x: e.clientX - r.left, y: e.clientY - r.top }
}

function onClick(e: MouseEvent) {
  const { x, y } = localXY(e)
  const id = axisAt(x, y)
  if (id) emit('selectAxis', id)
  else if (!(e.target as HTMLElement).closest('.u-over')) emit('selectChart')
}

function onDrop(e: DragEvent) {
  const ch = e.dataTransfer?.getData('text/channel')
  if (!ch) return
  const { x, y } = localXY(e)
  emit('dropChannel', ch, axisAt(x, y), e.dataTransfer?.getData('text/file') || null)
}

// ---- reactivity ---------------------------------------------------------------------------------------

const structure = computed(() =>
  JSON.stringify([
    props.chart.divisions,
    props.chart.axes,
    props.chart.lines.map((l) => [l.id, l.channel, l.mode, l.axisId, l.color, l.width, l.dash, l.marker]),
    props.chart.lines.map((l) => props.series.get(lineKey(l))?.values.length ?? -1),
    props.margin,
    props.stepBaseUs,
  ]),
)

watch(structure, () => nextTick(build))
watch(
  () => [props.selectedId, props.metrics],
  () => drawOverlay(),
)
watch(
  () => props.xWindow,
  () => {
    const w = xWin()
    view.value = w
    if (!u) return
    if (Math.abs((u.scales.x.min ?? NaN) - w[0]) < 1e-9 && Math.abs((u.scales.x.max ?? NaN) - w[1]) < 1e-9) return
    syncing = true
    u.setScale('x', { min: w[0], max: w[1] })
    syncing = false
    scheduleMarkers()
  },
)
watch(() => props.acceptedMarkers.size, updateMarkers)

onMounted(() => {
  build()
  ro = new ResizeObserver(() => {
    const el = host.value
    if (u && el) u.setSize({ width: el.clientWidth, height: el.clientHeight })
    drawOverlay()
  })
  if (body.value) ro.observe(body.value)
})
onBeforeUnmount(() => {
  ro?.disconnect()
  u?.destroy()
  cancelAnimationFrame(rafId)
})

/** Image of the graph as shown: plot, axes, cursors and a legend with the values of the active cursor (RF-36). */
function snapshot(): HTMLCanvasElement {
  const b = body.value!
  const h = host.value!
  const cursorCanvas = b.querySelector<HTMLCanvasElement>('.layer canvas')
  const legend: LegendItem[] = props.chart.lines.map((l) => ({
    text: lineName(l) + tag(l), color: l.color, width: l.width, dash: l.dash, marker: l.marker, value: props.readings[l.id]?.text, tag: lineTag(l, props.labels),
  }))
  return composeSnapshot(
    [
      { canvas: h.querySelector('canvas'), x: h.offsetLeft, y: 0, w: h.clientWidth, h: h.clientHeight },
      { canvas: overlay.value, x: 0, y: 0, w: b.clientWidth, h: b.clientHeight },
      { canvas: cursorCanvas, x: 0, y: 0, w: b.clientWidth, h: b.clientHeight },
    ],
    { w: b.clientWidth, h: b.clientHeight },
    legend,
  )
}

async function savePng() {
  downloadBlob(await canvasToBlob(snapshot()), `${safeFileName(props.tabName)}-graph-${props.index + 1}.png`)
}
defineExpose({ snapshot })

const axisName = (id: string) => {
  const i = props.chart.axes.findIndex((a) => a.id === id)
  return i < 0 ? '' : `Y${i + 1}`
}
const tag = (l: Line) => (l.mode === 'linear' ? '' : l.mode === 'average' ? ' · avg' : ' · step')
</script>

<template>
  <div class="panel" @click="emit('selectChart')">
    <div class="legend">
      <span class="idx">{{ index + 1 }}</span>
      <button
        v-for="l in chart.lines"
        :key="l.id"
        class="item"
        :class="{ sel: selectedId === l.id, loading: !dataOf(l) }"
        :style="{ width: legendWidth + 'px' }"
        @click.stop="emit('selectLine', l.id)"
      >
        <svg width="30" height="10" aria-hidden="true">
          <line x1="1" y1="5" x2="29" y2="5" :stroke="l.color" :stroke-width="Math.max(l.width, 1)" :stroke-dasharray="dashCss[l.dash]" />
          <circle v-if="l.marker" cx="15" cy="5" r="2.8" :fill="l.color" />
        </svg>
        <i v-if="lineTag(l, labels)" class="lab" :title="l.fileId === '~calc' ? 'Calculated channel' : 'Measurement ' + labels?.[l.fileId]">{{ lineTag(l, labels) }}</i>
        <span class="nm">{{ lineName(l) }}{{ tag(l) }}</span>
        <small>{{ axisName(l.axisId) }}</small>
        <b v-if="activeCursorId" class="val" :class="{ off: readings[l.id]?.off }">{{ readings[l.id]?.text ?? '' }}</b>
      </button>
      <span v-if="!chart.lines.length" class="hint">Drop channels here</span>
      <button class="png" title="Save this graph as a PNG image" @click.stop="savePng">PNG</button>
    </div>
    <div v-if="warnLines.length" class="warn" @click.stop>
      Markers requested for {{ warnLines.map((w) => `${w.name} (~${localeNumber(w.points)} points)`).join(', ') }}; drawing them on this many points may be slow.
      <button v-for="w in warnLines" :key="w.id + 'a'" @click="emit('markerAccept', w.id)">Draw {{ w.name }}</button>
      <button v-for="w in warnLines" :key="w.id + 'r'" @click="emit('markerReject', w.id)">Turn off {{ w.name }}</button>
    </div>
    <div ref="body" class="body" @click.stop="onClick" @dragover.prevent @drop.prevent="onDrop">
      <div ref="host" class="host" :style="{ left: margin.left - PAD + 'px', right: margin.right - PAD + 'px' }"></div>
      <canvas ref="overlay" class="overlay"></canvas>
      <CursorLayer
        :target="body ?? null"
        :geo="geo"
        :view="view"
        :cursors="cursors"
        :active-id="activeCursorId"
        :step-base-us="stepBaseUs"
        :kb="kb"
        @activate="emit('cursorActivate', $event)"
        @drag="(id, part, k) => emit('cursorDrag', id, part, k)"
        @create="(kind, k, k2) => emit('cursorCreate', kind, k, k2)"
        @go-to="emit('cursorGoTo', $event)"
      />
    </div>
  </div>
</template>

<style scoped>
.panel { display: flex; flex-direction: column; min-height: 0; flex: 1; border: 1px solid var(--border); border-radius: 6px; background: var(--bg); overflow: hidden; }
.legend { display: flex; flex-wrap: wrap; gap: 4px 8px; align-items: center; padding: 4px 8px; border-bottom: 1px solid var(--border); background: var(--panel); min-height: 28px; }
.idx { font-size: 11px; font-weight: 600; color: var(--muted); }
.item { display: inline-flex; align-items: center; gap: 5px; border: 1px solid transparent; background: transparent; border-radius: 4px; padding: 1px 6px; font-size: 12px; color: var(--text); }
.item:hover { border-color: var(--border); }
.item.sel { border-color: var(--accent); background: #fff; }
.item.loading { opacity: 0.5; }
.item { flex: none; }
.item .nm { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; text-align: left; }
.item small { color: var(--muted); }
.lab { font-style: normal; font-size: 10px; font-weight: 700; background: #e4e7ec; color: #344054; border-radius: 3px; padding: 0 4px; }
.val { font-variant-numeric: tabular-nums; min-width: 4.5em; text-align: right; font-weight: 500; }
.val.off { font-style: italic; color: var(--muted); }
.hint { color: var(--muted); font-size: 12px; }
.png { margin-left: auto; font-size: 11px; border: 1px solid var(--border); background: var(--bg); border-radius: 4px; padding: 0 6px; color: var(--muted); }
.warn { padding: 4px 8px; font-size: 12px; background: #fef0c7; color: #93370d; display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
.warn button { font-size: 12px; }
.body { position: relative; flex: 1; min-height: 0; }
.host { position: absolute; top: 0; bottom: 0; }
.overlay { position: absolute; inset: 0; pointer-events: none; }
</style>
