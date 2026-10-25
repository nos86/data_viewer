/** Axis geometry on divisions: bands, ranges, ticks, lanes and margins (RF-10…14, RI-26, RI-28). Presentation only. */
import { formatTicks } from './format'

export type Side = 'left' | 'right'

export interface AxisCfg {
  id: string
  side: Side
  band: [number, number] // divisions occupied, 0 = bottom of the plot
  min: number | null // null = automatic
  max: number | null
}

/** RI-28: axis on band [a, b] of D divisions with values min..max at its ends. */
export function bandRange(min: number, max: number, D: number, a: number, b: number): [number, number] {
  const h = (max - min) / (b - a)
  return [min - a * h, max + (D - b) * h]
}

/** Smallest "nice" step (1, 2, 2.5, 5 × 10^k) that is >= raw. */
export function niceStep(raw: number): number {
  if (!(raw > 0) || !isFinite(raw)) return 1
  const e = Math.floor(Math.log10(raw))
  for (const m of [1, 2, 2.5, 5, 10, 20]) {
    const s = m * 10 ** e
    if (s >= raw * (1 - 1e-12)) return s
  }
  return 10 ** (e + 2)
}

/** Automatic range rounded outward to nice values, spanning exactly nDiv divisions. */
export function autoRange(vmin: number, vmax: number, nDiv: number): [number, number] {
  if (!isFinite(vmin) || !isFinite(vmax)) return [0, nDiv]
  if (vmax <= vmin) {
    const pad = Math.abs(vmin) * 0.1 || 1
    vmin -= pad
    vmax += pad
  }
  let step = niceStep((vmax - vmin) / nDiv)
  for (let i = 0; i < 6; i++) {
    const lo = Math.floor(vmin / step + 1e-9) * step
    if (lo + step * nDiv >= vmax - step * 1e-9) return [lo, lo + step * nDiv]
    step = niceStep(step * 1.01)
  }
  return [vmin, vmax]
}

/** Values at the two ends of the band (division a and division b). */
export function axisEndValues(axis: AxisCfg, dataMin: number, dataMax: number): [number, number] {
  const n = axis.band[1] - axis.band[0]
  if (axis.min != null && axis.max != null) return axis.max > axis.min ? [axis.min, axis.max] : [axis.min, axis.min + 1]
  const [lo, hi] = autoRange(dataMin, dataMax, n) // auto = range of the whole channel(s), not the zoom window (RI-26)
  if (axis.min != null) return [axis.min, Math.max(hi, axis.min + (hi - lo))]
  if (axis.max != null) return [Math.min(lo, axis.max - (hi - lo)), axis.max]
  return [lo, hi]
}

export interface AxisTicks {
  divisions: number[] // division indices a..b
  values: number[]
  labels: string[]
  step: number
  scale: [number, number] // full y-scale range of the plot (RI-28)
}

export function axisTicks(axis: AxisCfg, D: number, dataMin: number, dataMax: number): AxisTicks {
  const [a, b] = axis.band
  const [lo, hi] = axisEndValues(axis, dataMin, dataMax)
  const step = (hi - lo) / (b - a)
  const divisions = Array.from({ length: b - a + 1 }, (_, i) => a + i)
  const values = divisions.map((_, i) => lo + i * step)
  const maxAbs = Math.max(Math.abs(lo), Math.abs(hi))
  return { divisions, values, labels: formatTicks(values, maxAbs, step), step, scale: bandRange(lo, hi, D, a, b) }
}

export interface LanePlacement {
  lane: number
  omitTop: boolean // adjacent band above shares the lane: drop this axis' top label
}

/** Lanes per side (RF-13): overlapping bands go to different lanes, adjacent bands share one. */
export function assignLanes(axes: AxisCfg[]): Map<string, LanePlacement> {
  const out = new Map<string, LanePlacement>()
  for (const side of ['left', 'right'] as Side[]) {
    const lanes: AxisCfg[][] = []
    for (const ax of axes.filter((x) => x.side === side).sort((p, q) => p.band[0] - q.band[0] || q.band[1] - p.band[1])) {
      let k = lanes.findIndex((l) => l.every((o) => ax.band[0] >= o.band[1] || o.band[0] >= ax.band[1]))
      if (k < 0) k = lanes.push([]) - 1
      lanes[k].push(ax)
      out.set(ax.id, { lane: k, omitTop: false })
    }
    for (const l of lanes) {
      for (const ax of l) if (l.some((o) => o !== ax && o.band[0] === ax.band[1])) out.get(ax.id)!.omitTop = true
    }
  }
  return out
}

export const PAD = 14 // horizontal padding inside each uPlot canvas so the first/last time label is not clipped
export const FONT = '11px system-ui, -apple-system, "Segoe UI", sans-serif'
export const TICK_LEN = 5
export const LANE_PAD = 8

export interface ChartMetrics {
  placement: Map<string, LanePlacement>
  ticks: Map<string, AxisTicks>
  laneWidth: { left: number[]; right: number[] }
  margin: { left: number; right: number }
}

export type Measure = (text: string) => number

/** Lane widths from the formatted min/max of each axis, sign included (RI-26). `data` gives channel ranges per axis. */
export function chartMetrics(
  axes: AxisCfg[],
  D: number,
  data: (axisId: string) => [number, number],
  measure: Measure,
): ChartMetrics {
  const placement = assignLanes(axes)
  const ticks = new Map<string, AxisTicks>()
  const laneWidth = { left: [] as number[], right: [] as number[] }
  for (const ax of axes) {
    const [dmin, dmax] = data(ax.id)
    const t = axisTicks(ax, D, dmin, dmax)
    ticks.set(ax.id, t)
    const p = placement.get(ax.id)!
    const w = Math.max(...t.labels.map(measure), 0) + TICK_LEN + LANE_PAD
    const arr = laneWidth[ax.side]
    arr[p.lane] = Math.max(arr[p.lane] ?? 0, w)
  }
  const sum = (a: number[]) => a.reduce((s, v) => s + (v ?? 0), 0)
  return { placement, ticks, laneWidth, margin: { left: sum(laneWidth.left), right: sum(laneWidth.right) } }
}

/** Margins of a tab = maximum over its charts, so all plot areas line up (RF-10). */
export function tabMargins(metrics: ChartMetrics[]): { left: number; right: number } {
  return {
    left: Math.max(PAD, ...metrics.map((m) => m.margin.left)),
    right: Math.max(PAD, ...metrics.map((m) => m.margin.right)),
  }
}

/** Distance from the plot edge to the near edge of a lane. */
export function laneOffset(widths: number[], lane: number): number {
  let o = 0
  for (let i = 0; i < lane; i++) o += widths[i] ?? 0
  return o
}
