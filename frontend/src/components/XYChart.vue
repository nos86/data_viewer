<script setup lang="ts">
/** XY graph (RF-18): own canvas, outside the time-axis alignment. Points are the times the X and Y channels share. */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { ChannelOut, SeriesData } from '../api/client'
import { axisTicks, FONT, type AxisCfg } from '../composables/axes'
import { cursorColor, type Cursor } from '../composables/cursors'
import { lineName, lineTag, seriesKey, type Chart, type Dash, type Line } from '../composables/layout'
import { pointIndexAt, segmentRange, xyPoints, type XYPoints } from '../composables/xy'
import { canvasToBlob, composeSnapshot, downloadBlob, safeFileName, type LegendItem } from '../composables/chartImage'

const props = defineProps<{
  chart: Chart
  series: Map<string, SeriesData>
  shifts: Record<string, number>
  channels: Map<string, ChannelOut> // key: fileId|channel
  cursors: Cursor[]
  activeCursorId: string | null
  labels: Record<string, string> | null
  selectedId: string | null
  index: number
  legendWidth: number
  tabName: string
}>()
const emit = defineEmits<{
  selectLine: [id: string]
  selectChart: []
  dropChannel: [channel: string, axisId: string | null, fileId: string | null]
}>()

const MAX_MARKERS = 20_000
const cv = ref<HTMLCanvasElement>()
const body = ref<HTMLDivElement>()
const view = ref<{ x: [number, number]; y: [number, number] } | null>(null) // rectangle zoom; null = full range
let raf = 0
let ro: ResizeObserver | null = null
let rubber: { x0: number; y0: number; x1: number; y1: number } | null = null

const dashes: Record<Dash, number[]> = { solid: [], dashed: [9, 5], dotted: [2, 4] }
const dashCss: Record<Dash, string> = { solid: '', dashed: '6 3', dotted: '1.5 3' }
const D = computed(() => props.chart.divisions)

const xRef = computed(() => props.chart.xy?.x ?? null)
const xName = computed(() => (xRef.value ? (props.channels.get(`${xRef.value.fileId}|${xRef.value.channel}`)?.name ?? xRef.value.channel) : ''))
const xSeries = computed(() => (xRef.value ? props.series.get(seriesKey(xRef.value.fileId, xRef.value.channel, xRef.value.mode)) : undefined))

/** Points per Y line: common times only, holes dropped (RF-18). */
const points = computed(() => {
  const out = new Map<string, XYPoints>()
  const xr = xRef.value
  const xs = xSeries.value
  if (!xr || !xs) return out
  const X = { n: xs.n, jStart: xs.jStart, values: xs.values, shiftK: props.shifts[xr.fileId] ?? 0 }
  for (const l of props.chart.lines) {
    const ys = props.series.get(seriesKey(l.fileId, l.channel, l.mode))
    if (ys) out.set(l.id, xyPoints(X, { n: ys.n, jStart: ys.jStart, values: ys.values, shiftK: props.shifts[l.fileId] ?? 0 }))
  }
  return out
})

const range = (refs: { fileId: string; channel: string }[]): [number, number] => {
  let lo = Infinity
  let hi = -Infinity
  for (const r of refs) {
    const c = props.channels.get(`${r.fileId}|${r.channel}`)
    if (c?.v_min == null || c.v_max == null) continue
    lo = Math.min(lo, c.v_min)
    hi = Math.max(hi, c.v_max)
  }
  return isFinite(lo) ? [lo, hi] : [0, 1]
}

const xTicks = computed(() => {
  const v = view.value
  const cfg: AxisCfg = { id: 'x', side: 'left', band: [0, D.value], min: v ? v.x[0] : (props.chart.xy?.xMin ?? null), max: v ? v.x[1] : (props.chart.xy?.xMax ?? null) }
  return axisTicks(cfg, D.value, ...range(xRef.value ? [xRef.value] : []))
})
const yTicks = computed(() => {
  const ax = props.chart.axes[0]
  const v = view.value
  const cfg: AxisCfg = { id: 'y', side: 'left', band: [0, D.value], min: v ? v.y[0] : (ax?.min ?? null), max: v ? v.y[1] : (ax?.max ?? null) }
  return axisTicks(cfg, D.value, ...range(props.chart.lines))
})

const noCommon = (l: Line) => !!xSeries.value && points.value.has(l.id) && points.value.get(l.id)!.k.length === 0

interface Rect {
  L: number
  T: number
  R: number
  B: number
}
let rect: Rect = { L: 0, T: 0, R: 0, B: 0 }

function draw() {
  const el = cv.value
  const b = body.value
  if (!el || !b) return
  const dpr = devicePixelRatio
  const W = b.clientWidth
  const H = b.clientHeight
  if (el.width !== Math.round(W * dpr) || el.height !== Math.round(H * dpr)) {
    el.width = Math.round(W * dpr)
    el.height = Math.round(H * dpr)
    el.style.width = W + 'px'
    el.style.height = H + 'px'
  }
  const ctx = el.getContext('2d')!
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, W, H)
  ctx.font = FONT
  const xt = xTicks.value
  const yt = yTicks.value
  const left = Math.max(...yt.labels.map((t) => ctx.measureText(t).width), 10) + 16
  rect = { L: left, T: 8, R: W - 16, B: H - 26 }
  const { L, T, R, B } = rect
  if (R <= L || B <= T) return
  const [x0, x1] = [xt.values[0], xt.values[xt.values.length - 1]]
  const [y0, y1] = [yt.values[0], yt.values[yt.values.length - 1]]
  const px = (v: number) => L + ((v - x0) / (x1 - x0)) * (R - L)
  const py = (v: number) => B - ((v - y0) / (y1 - y0)) * (B - T)

  // grid and axes: ticks on the divisions
  ctx.strokeStyle = '#e3e6ea'
  ctx.fillStyle = '#667085'
  ctx.lineWidth = 1
  ctx.textBaseline = 'middle'
  ctx.beginPath()
  xt.values.forEach((_, i) => {
    const x = Math.round(L + (i / D.value) * (R - L)) + 0.5
    ctx.moveTo(x, T)
    ctx.lineTo(x, B)
  })
  yt.values.forEach((_, i) => {
    const y = Math.round(B - (i / D.value) * (B - T)) + 0.5
    ctx.moveTo(L, y)
    ctx.lineTo(R, y)
  })
  ctx.stroke()
  ctx.textAlign = 'right'
  yt.labels.forEach((t, i) => ctx.fillText(t, L - 6, B - (i / D.value) * (B - T)))
  ctx.textAlign = 'center'
  ctx.textBaseline = 'top'
  xt.labels.forEach((t, i) => ctx.fillText(t, L + (i / D.value) * (R - L), B + 6))
  ctx.strokeStyle = '#98a2b3'
  ctx.strokeRect(L + 0.5, T + 0.5, R - L, B - T)

  ctx.save()
  ctx.beginPath()
  ctx.rect(L, T, R - L, B - T)
  ctx.clip()
  for (const l of props.chart.lines) {
    const p = points.value.get(l.id)
    if (!p || !p.k.length) continue
    ctx.strokeStyle = l.color
    ctx.fillStyle = l.color
    ctx.lineWidth = l.width
    ctx.setLineDash(dashes[l.dash])
    ctx.beginPath()
    for (let i = 0; i < p.k.length; i++) {
      const X = px(p.x[i])
      const Y = py(p.y[i])
      if (i === 0) ctx.moveTo(X, Y)
      else ctx.lineTo(X, Y)
    }
    ctx.stroke()
    ctx.setLineDash([])
    if (l.marker && p.k.length <= MAX_MARKERS) {
      for (let i = 0; i < p.k.length; i++) {
        ctx.beginPath()
        ctx.arc(px(p.x[i]), py(p.y[i]), 2.5, 0, 6.2832)
        ctx.fill()
      }
    }
    // cursors: a band highlights its stretch of the path, a single cursor its numbered point
    for (const c of props.cursors) {
      const col = cursorColor(c)
      if (c.kind === 'band') {
        const seg = segmentRange(p, c.k, c.k2)
        if (!seg) continue
        ctx.strokeStyle = col
        ctx.lineWidth = l.width + 2.5
        ctx.globalAlpha = c.id === props.activeCursorId ? 0.85 : 0.5
        ctx.beginPath()
        for (let i = seg[0]; i <= seg[1]; i++) (i === seg[0] ? ctx.moveTo : ctx.lineTo).call(ctx, px(p.x[i]), py(p.y[i]))
        ctx.stroke()
        ctx.globalAlpha = 1
      }
    }
  }
  ctx.restore()
  // numbered points are drawn unclipped so the number stays readable at the border
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  for (const l of props.chart.lines) {
    const p = points.value.get(l.id)
    if (!p || !p.k.length) continue
    for (const c of props.cursors) {
      if (c.kind !== 'single') continue
      const i = pointIndexAt(p, c.k)
      if (i < 0) continue
      const X = px(p.x[i])
      const Y = py(p.y[i])
      if (X < L - 2 || X > R + 2 || Y < T - 2 || Y > B + 2) continue
      const col = cursorColor(c)
      ctx.fillStyle = c.id === props.activeCursorId ? col : '#fff'
      ctx.strokeStyle = col
      ctx.lineWidth = 2
      ctx.beginPath()
      ctx.arc(X, Y, 8, 0, 6.2832)
      ctx.fill()
      ctx.stroke()
      ctx.fillStyle = c.id === props.activeCursorId ? '#fff' : col
      ctx.fillText(String(c.number), X, Y + 0.5)
    }
  }
  if (rubber) {
    ctx.fillStyle = 'rgba(0,114,178,0.12)'
    ctx.strokeStyle = 'rgba(0,114,178,0.6)'
    const x = Math.min(rubber.x0, rubber.x1)
    const y = Math.min(rubber.y0, rubber.y1)
    ctx.fillRect(x, y, Math.abs(rubber.x1 - rubber.x0), Math.abs(rubber.y1 - rubber.y0))
    ctx.strokeRect(x, y, Math.abs(rubber.x1 - rubber.x0), Math.abs(rubber.y1 - rubber.y0))
  }
}

const schedule = () => {
  cancelAnimationFrame(raf)
  raf = requestAnimationFrame(draw)
}

function local(e: MouseEvent) {
  const r = body.value!.getBoundingClientRect()
  return { x: e.clientX - r.left, y: e.clientY - r.top }
}

function onDown(e: MouseEvent) {
  if (e.button !== 0) return
  const p = local(e)
  const { L, T, R, B } = rect
  if (p.x < L || p.x > R || p.y < T || p.y > B) return
  e.preventDefault()
  rubber = { x0: p.x, y0: p.y, x1: p.x, y1: p.y }
  const move = (ev: MouseEvent) => {
    const q = local(ev)
    rubber = { x0: p.x, y0: p.y, x1: Math.max(L, Math.min(R, q.x)), y1: Math.max(T, Math.min(B, q.y)) }
    schedule()
  }
  const up = () => {
    window.removeEventListener('mousemove', move)
    window.removeEventListener('mouseup', up)
    const r = rubber
    rubber = null
    if (r && Math.abs(r.x1 - r.x0) > 6 && Math.abs(r.y1 - r.y0) > 6) {
      const xt = xTicks.value
      const yt = yTicks.value
      const [xa, xb] = [xt.values[0], xt.values[xt.values.length - 1]]
      const [ya, yb] = [yt.values[0], yt.values[yt.values.length - 1]]
      const vx = (x: number) => xa + ((x - L) / (R - L)) * (xb - xa)
      const vy = (y: number) => ya + ((B - y) / (B - T)) * (yb - ya)
      view.value = {
        x: [vx(Math.min(r.x0, r.x1)), vx(Math.max(r.x0, r.x1))],
        y: [vy(Math.max(r.y0, r.y1)), vy(Math.min(r.y0, r.y1))],
      }
    }
    schedule()
  }
  window.addEventListener('mousemove', move)
  window.addEventListener('mouseup', up)
}

function onDrop(e: DragEvent) {
  const ch = e.dataTransfer?.getData('text/channel')
  if (ch) emit('dropChannel', ch, null, e.dataTransfer?.getData('text/file') || null)
}

watch(() => [props.chart, props.series, props.shifts, props.cursors, props.activeCursorId, props.channels, view.value], schedule, { deep: true })
watch(
  () => [props.chart.xy?.x, props.chart.divisions],
  () => (view.value = null),
  { deep: true },
)
onMounted(() => {
  ro = new ResizeObserver(schedule)
  if (body.value) {
    ro.observe(body.value)
    body.value.addEventListener('mousedown', onDown)
  }
  schedule()
})
onBeforeUnmount(() => {
  ro?.disconnect()
  body.value?.removeEventListener('mousedown', onDown)
  cancelAnimationFrame(raf)
})

const name = (l: Line) => lineName(l)

/** Image of the graph with its legend (RF-36). */
function snapshot(): HTMLCanvasElement {
  const b = body.value!
  const legend: LegendItem[] = props.chart.lines.map((l) => ({ text: name(l), color: l.color, width: l.width, dash: l.dash, marker: l.marker, tag: lineTag(l, props.labels) }))
  const xl = xRef.value ? `X: ${xRef.value.fileId === '~calc' ? xName.value : xRef.value.channel}` : ''
  return composeSnapshot([{ canvas: cv.value, x: 0, y: 0, w: b.clientWidth, h: b.clientHeight }], { w: b.clientWidth, h: b.clientHeight }, xl ? [{ text: xl, color: '#667085', width: 1, dash: 'solid', marker: false }, ...legend] : legend)
}
async function savePng() {
  downloadBlob(await canvasToBlob(snapshot()), `${safeFileName(props.tabName)}-xy-${props.index + 1}.png`)
}
defineExpose({ snapshot })
</script>

<template>
  <div class="panel" @click="emit('selectChart')">
    <div class="legend">
      <span class="idx">{{ index + 1 }} · XY</span>
      <span class="x" :title="'X channel'">
        X:
        <template v-if="xRef">
          <i v-if="labels || xRef.fileId === '~calc'" class="lab">{{ xRef.fileId === '~calc' ? 'ƒ' : labels?.[xRef.fileId] }}</i>{{ xRef.fileId === '~calc' ? xName : xRef.channel }}
        </template>
        <template v-else><em>choose an X channel in Properties</em></template>
      </span>
      <span class="vs">→</span>
      <button v-for="l in chart.lines" :key="l.id" class="item" :class="{ sel: selectedId === l.id }" :style="{ minWidth: legendWidth * 0.7 + 'px' }" @click.stop="emit('selectLine', l.id)">
        <svg width="28" height="10" aria-hidden="true">
          <line x1="1" y1="5" x2="27" y2="5" :stroke="l.color" :stroke-width="Math.max(l.width, 1)" :stroke-dasharray="dashCss[l.dash]" />
          <circle v-if="l.marker" cx="14" cy="5" r="2.8" :fill="l.color" />
        </svg>
        <i v-if="lineTag(l, labels)" class="lab">{{ lineTag(l, labels) }}</i>
        <span>{{ name(l) }}</span>
        <small v-if="noCommon(l)" class="warn" title="The two channels have no coincident grid points (check the shifts)">no common times</small>
      </button>
      <span v-if="!chart.lines.length" class="hint">Drop channels here to plot them against X</span>
      <button v-if="view" class="reset" @click.stop="view = null">Reset zoom</button>
      <button class="png" title="Save this graph as a PNG image" @click.stop="savePng">PNG</button>
    </div>
    <div ref="body" class="body" @dragover.prevent @drop.prevent="onDrop" @dblclick="view = null">
      <canvas ref="cv"></canvas>
    </div>
  </div>
</template>

<style scoped>
.panel { display: flex; flex-direction: column; min-height: 0; flex: 1; border: 1px solid var(--border); border-radius: 6px; background: var(--bg); overflow: hidden; }
.legend { display: flex; flex-wrap: wrap; gap: 4px 8px; align-items: center; padding: 4px 8px; border-bottom: 1px solid var(--border); background: var(--panel); min-height: 28px; font-size: 12px; }
.idx { font-size: 11px; font-weight: 600; color: var(--muted); }
.x { font-weight: 500; }
.vs { color: var(--muted); }
.item { display: inline-flex; align-items: center; gap: 5px; border: 1px solid transparent; background: transparent; border-radius: 4px; padding: 1px 6px; font-size: 12px; color: var(--text); }
.item:hover { border-color: var(--border); }
.item.sel { border-color: var(--accent); background: #fff; }
.lab { font-style: normal; font-size: 10px; font-weight: 700; background: #e4e7ec; color: #344054; border-radius: 3px; padding: 0 4px; margin-right: 3px; }
.warn { color: #93370d; font-weight: 600; }
.hint { color: var(--muted); }
.png { font-size: 11px; border: 1px solid var(--border); background: var(--bg); border-radius: 4px; padding: 0 6px; color: var(--muted); }
.reset { margin-left: auto; font-size: 12px; border: none; background: none; color: var(--accent); }
.body { position: relative; flex: 1; min-height: 0; cursor: crosshair; }
canvas { position: absolute; inset: 0; }
</style>
