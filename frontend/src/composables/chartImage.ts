/** PNG of a graph (RF-36): the layers that make up a graph on screen plus a legend drawn on the canvas. */
import type { Dash } from './layout'

export interface LegendItem {
  text: string
  color: string
  width: number
  dash: Dash
  marker: boolean
  value?: string // reading of the active cursor
  tag?: string | null // A / B / ƒ
}

export interface Layer {
  canvas: HTMLCanvasElement | null | undefined
  x: number // CSS pixels from the left of the graph body
  y: number
  w: number // CSS size the layer is shown at
  h: number
}

const DASH: Record<Dash, number[]> = { solid: [], dashed: [9, 5], dotted: [2, 4] }
const FONT = '12px system-ui, -apple-system, "Segoe UI", sans-serif'
const PAD = 8
const ROW = 20

/** Lays the legend out in rows; returns the height used (and draws when a context is given). */
export function drawLegend(ctx: CanvasRenderingContext2D, items: LegendItem[], width: number, y0: number, draw: boolean): number {
  ctx.font = FONT
  let x = PAD
  let y = y0 + PAD
  for (const it of items) {
    const label = (it.tag ? it.tag + ' ' : '') + it.text + (it.value ? '  ' + it.value : '')
    const w = 34 + ctx.measureText(label).width + 14
    if (x + w > width - PAD && x > PAD) {
      x = PAD
      y += ROW
    }
    if (draw) {
      ctx.strokeStyle = it.color
      ctx.fillStyle = it.color
      ctx.lineWidth = Math.max(1, it.width)
      ctx.setLineDash(DASH[it.dash])
      ctx.beginPath()
      ctx.moveTo(x, y + 8)
      ctx.lineTo(x + 28, y + 8)
      ctx.stroke()
      ctx.setLineDash([])
      if (it.marker) {
        ctx.beginPath()
        ctx.arc(x + 14, y + 8, 3, 0, 6.2832)
        ctx.fill()
      }
      ctx.fillStyle = '#1c2026'
      ctx.textBaseline = 'middle'
      ctx.fillText(label, x + 34, y + 8)
    }
    x += w
  }
  return items.length ? y - y0 + ROW + PAD : 0
}

/** Composite of the layers (uPlot canvas, axes, cursors...) under a legend, at the screen's pixel ratio. */
export function composeSnapshot(layers: Layer[], size: { w: number; h: number }, legend: LegendItem[]): HTMLCanvasElement {
  const dpr = window.devicePixelRatio || 1
  const probe = document.createElement('canvas').getContext('2d')!
  const legendH = drawLegend(probe, legend, size.w, 0, false)
  const out = document.createElement('canvas')
  out.width = Math.round(size.w * dpr)
  out.height = Math.round((size.h + legendH) * dpr)
  const ctx = out.getContext('2d')!
  ctx.scale(dpr, dpr)
  ctx.fillStyle = '#ffffff'
  ctx.fillRect(0, 0, size.w, size.h + legendH)
  drawLegend(ctx, legend, size.w, 0, true)
  for (const l of layers) if (l.canvas) ctx.drawImage(l.canvas, l.x, legendH + l.y, l.w, l.h)
  ctx.strokeStyle = '#d6d9de'
  ctx.lineWidth = 1
  ctx.strokeRect(0.5, 0.5, size.w - 1, size.h + legendH - 1)
  return out
}

export function canvasToBlob(c: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => c.toBlob((b) => (b ? resolve(b) : reject(new Error('The image could not be created'))), 'image/png'))
}

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 5000)
}

export const safeFileName = (s: string) => s.replace(/[^A-Za-z0-9._ -]+/g, '_').trim().slice(0, 80) || 'graph'
