<script setup lang="ts">
/** Cursors on their own canvas above the plot, independent of uPlot rendering (RI-29). */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { clampK, cursorColor, kToSeconds, outOfView, snapK, type Cursor } from '../composables/cursors'
import { FONT } from '../composables/axes'

export interface Geo {
  left: number
  right: number
  top: number
  height: number
}

const props = defineProps<{
  target: HTMLElement | null // element receiving the pointer events (the graph body)
  geo: Geo | null
  view: [number, number] // visible time window, seconds
  cursors: Cursor[]
  activeId: string | null
  stepBaseUs: number
  kb: { min: number; max: number }
}>()
const emit = defineEmits<{
  activate: [id: string]
  drag: [id: string, part: 'single' | 'start' | 'end' | 'whole', k: number]
  create: [kind: 'single' | 'band', k: number, k2: number]
  goTo: [id: string]
}>()

const cv = ref<HTMLCanvasElement>()
let raf = 0
let preview: { k1: number; k2: number } | null = null
const BADGE_H = 15
const HIT = 5

const xOf = (g: Geo, t: number) => g.left + ((t - props.view[0]) / (props.view[1] - props.view[0])) * (g.right - g.left)
const tOf = (g: Geo, x: number) => props.view[0] + ((x - g.left) / (g.right - g.left)) * (props.view[1] - props.view[0])
const kOf = (g: Geo, x: number) => clampK(snapK(tOf(g, x), props.stepBaseUs), props.kb)
const xk = (g: Geo, k: number) => xOf(g, kToSeconds(k, props.stepBaseUs))

function draw() {
  const el = cv.value
  const g = props.geo
  const parent = el?.parentElement
  if (!el || !g || !parent) return
  const dpr = devicePixelRatio
  const W = parent.clientWidth
  const H = parent.clientHeight
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
  ctx.textBaseline = 'middle'
  ctx.textAlign = 'center'
  const bottom = g.top + g.height
  let leftArrows = 0
  let rightArrows = 0

  if (preview) {
    const a = xk(g, Math.min(preview.k1, preview.k2))
    const b = xk(g, Math.max(preview.k1, preview.k2))
    ctx.fillStyle = 'rgba(80,80,80,0.12)'
    ctx.fillRect(a, g.top, Math.max(1, b - a), g.height)
  }

  for (const c of props.cursors) {
    const col = cursorColor(c)
    const active = c.id === props.activeId
    const side = outOfView(c, props.view, props.stepBaseUs)
    if (side) {
      // RF-25: marker on the border with the cursor number; click to go there
      const n = side === 'left' ? leftArrows++ : rightArrows++
      const y = g.top + 10 + n * 18
      const x = side === 'left' ? g.left + 2 : g.right - 2
      ctx.fillStyle = col
      ctx.beginPath()
      if (side === 'left') {
        ctx.moveTo(x, y)
        ctx.lineTo(x + 9, y - 6)
        ctx.lineTo(x + 9, y + 6)
      } else {
        ctx.moveTo(x, y)
        ctx.lineTo(x - 9, y - 6)
        ctx.lineTo(x - 9, y + 6)
      }
      ctx.closePath()
      ctx.fill()
      ctx.textAlign = side === 'left' ? 'left' : 'right'
      ctx.fillText(String(c.number), side === 'left' ? x + 12 : x - 12, y)
      ctx.textAlign = 'center'
      continue
    }
    const x1 = xk(g, c.k)
    const x2 = xk(g, c.k2)
    ctx.save()
    ctx.beginPath()
    ctx.rect(g.left, g.top, g.right - g.left, g.height)
    ctx.clip()
    if (c.kind === 'band') {
      ctx.fillStyle = col + (active ? '33' : '1f')
      ctx.fillRect(x1, g.top, Math.max(1, x2 - x1), g.height)
    }
    ctx.strokeStyle = col
    ctx.lineWidth = active ? 2 : 1
    ctx.beginPath()
    for (const x of c.kind === 'band' ? [x1, x2] : [x1]) {
      ctx.moveTo(Math.round(x) + 0.5, g.top)
      ctx.lineTo(Math.round(x) + 0.5, bottom)
    }
    ctx.stroke()
    ctx.restore()
    // numbered badge on the time axis (RF-24)
    const bx = c.kind === 'band' ? (x1 + x2) / 2 : x1
    if (bx >= g.left - 8 && bx <= g.right + 8) {
      const w = 8 + String(c.number).length * 7
      ctx.fillStyle = active ? col : '#fff'
      ctx.strokeStyle = col
      ctx.lineWidth = 1.5
      ctx.beginPath()
      ctx.roundRect(bx - w / 2, bottom + 2, w, BADGE_H, 4)
      ctx.fill()
      ctx.stroke()
      ctx.fillStyle = active ? '#fff' : col
      ctx.fillText(String(c.number), bx, bottom + 2 + BADGE_H / 2 + 0.5)
    }
  }
}

const schedule = () => {
  cancelAnimationFrame(raf)
  raf = requestAnimationFrame(draw)
}

type Hit = { kind: 'arrow'; id: string } | { kind: 'badge'; c: Cursor } | { kind: 'edge'; c: Cursor; part: 'single' | 'start' | 'end' }

function hitTest(x: number, y: number): Hit | null {
  const g = props.geo
  if (!g) return null
  const bottom = g.top + g.height
  let l = 0
  let r = 0
  for (const c of [...props.cursors].reverse()) {
    const side = outOfView(c, props.view, props.stepBaseUs)
    if (side) {
      const n = side === 'left' ? l++ : r++
      const ay = g.top + 10 + n * 18
      const ax = side === 'left' ? g.left : g.right
      if (Math.abs(y - ay) <= 8 && Math.abs(x - ax) <= 22) return { kind: 'arrow', id: c.id }
    }
  }
  for (const c of [...props.cursors].reverse()) {
    if (outOfView(c, props.view, props.stepBaseUs)) continue
    const x1 = xk(g, c.k)
    const x2 = xk(g, c.k2)
    const bx = c.kind === 'band' ? (x1 + x2) / 2 : x1
    if (y >= bottom && y <= bottom + BADGE_H + 4 && Math.abs(x - bx) <= 12) return { kind: 'badge', c }
    if (y >= g.top && y <= bottom) {
      if (c.kind === 'single' && Math.abs(x - x1) <= HIT) return { kind: 'edge', c, part: 'single' }
      if (c.kind === 'band') {
        if (Math.abs(x - x2) <= HIT) return { kind: 'edge', c, part: 'end' }
        if (Math.abs(x - x1) <= HIT) return { kind: 'edge', c, part: 'start' }
      }
    }
  }
  return null
}

const local = (e: MouseEvent) => {
  const r = props.target!.getBoundingClientRect()
  return { x: e.clientX - r.left, y: e.clientY - r.top }
}

function onDown(e: MouseEvent) {
  const g = props.geo
  if (!g || e.button !== 0) return
  const { x, y } = local(e)
  const inPlot = x >= g.left && x <= g.right && y >= g.top && y <= g.top + g.height
  const hit = hitTest(x, y)
  if (hit?.kind === 'arrow') {
    e.stopPropagation()
    emit('activate', hit.id)
    emit('goTo', hit.id)
    return
  }
  if (hit) {
    e.stopPropagation()
    e.preventDefault()
    const id = hit.kind === 'badge' ? hit.c.id : hit.c.id
    emit('activate', id)
    const grab = kOf(g, x)
    const start = hit.kind === 'badge' ? hit.c.k : 0
    const move = (ev: MouseEvent) => {
      const k = kOf(g, local(ev).x)
      if (hit.kind === 'edge') emit('drag', id, hit.part, k)
      else if (hit.c.kind === 'band') emit('drag', id, 'whole', start + (k - grab))
      else emit('drag', id, 'single', k)
    }
    track(move)
    return
  }
  if (e.altKey && inPlot) {
    e.stopPropagation()
    e.preventDefault()
    const k0 = kOf(g, x)
    const x0 = x
    let moved = false
    const move = (ev: MouseEvent) => {
      const p = local(ev)
      if (Math.abs(p.x - x0) > 3) moved = true
      preview = moved ? { k1: k0, k2: kOf(g, p.x) } : null
      schedule()
    }
    track(move, (ev) => {
      const k1 = kOf(g, local(ev).x)
      preview = null
      if (moved && k1 !== k0) emit('create', 'band', Math.min(k0, k1), Math.max(k0, k1))
      else emit('create', 'single', k0, k0)
      schedule()
    })
  }
}

function track(move: (e: MouseEvent) => void, up?: (e: MouseEvent) => void) {
  const u = (ev: MouseEvent) => {
    window.removeEventListener('mousemove', move)
    window.removeEventListener('mouseup', u)
    up?.(ev)
  }
  window.addEventListener('mousemove', move)
  window.addEventListener('mouseup', u)
}

function onHover(e: MouseEvent) {
  const t = props.target
  const g = props.geo
  if (!t || !g || e.buttons) return
  const { x, y } = local(e)
  const hit = hitTest(x, y)
  const want = hit ? (hit.kind === 'edge' ? 'ew-resize' : 'pointer') : ''
  if (t.style.cursor !== want) t.style.cursor = want
}

let attached: HTMLElement | null = null
function attach() {
  if (attached === props.target) return
  detach()
  attached = props.target
  attached?.addEventListener('mousedown', onDown, true)
  attached?.addEventListener('mousemove', onHover)
}
function detach() {
  attached?.removeEventListener('mousedown', onDown, true)
  attached?.removeEventListener('mousemove', onHover)
  attached = null
}

watch(() => props.target, attach)
watch(() => [props.geo, props.view, props.activeId, props.stepBaseUs], schedule, { deep: true })
watch(() => props.cursors, schedule, { deep: true })
onMounted(() => {
  attach()
  schedule()
})
onBeforeUnmount(() => {
  detach()
  cancelAnimationFrame(raf)
})
</script>

<template>
  <div class="layer"><canvas ref="cv"></canvas></div>
</template>

<style scoped>
.layer { position: absolute; inset: 0; pointer-events: none; }
</style>
