/** Layout model: tabs → up to 4 stacked charts → axes and lines (RF-09…16). Pure functions over plain objects. */
import type { AxisCfg, Side } from './axes'
import { PALETTE, styleFor } from './palette'

export { PALETTE }

export type Mode = 'step' | 'linear' | 'average'
export type Dash = 'solid' | 'dashed' | 'dotted'

export const MAX_CHARTS = 4
export const DEFAULT_DIVISIONS = 10
export interface SeriesRef {
  fileId: string
  channel: string
  mode: Mode
}

export const CALC_FILE = '~calc' // pseudo measurement of calculated channels: `channel` is then the formula id (typed ref 'calc')

export interface Line {
  id: string
  fileId: string // measurement the channel belongs to, or CALC_FILE
  channel: string
  title?: string // display name of a calculated channel
  mode: Mode
  axisId: string
  color: string
  width: number
  dash: Dash
  marker: boolean
}

/** XY graph (RF-18): `lines` are the Y series, `x` the common X channel; its single axis carries the Y range. */
export interface XYConfig {
  x: SeriesRef | null
  xMin: number | null // null = automatic
  xMax: number | null
}

export interface Chart {
  id: string
  kind: 'time' | 'xy'
  divisions: number
  axes: AxisCfg[]
  lines: Line[]
  xy?: XYConfig
}

export interface Tab {
  id: string
  name: string
  charts: Chart[]
}

export interface Layout {
  tabs: Tab[]
}

export const isCalc = (l: { fileId: string }) => l.fileId === CALC_FILE
export const lineName = (l: Line) => l.title ?? l.channel
/** Measurement tag of a legend entry: A/B… when several are open, ƒ for calculated channels (RF-17). */
export const lineTag = (l: Line, labels: Record<string, string> | null) => (isCalc(l) ? 'ƒ' : labels ? labels[l.fileId] : null)

let counter = 0
export const uid = (p: string) => `${p}${++counter}`

/** After restoring a layout, make new ids continue after the largest restored one. */
export function syncUid(layout: Layout): void {
  const ids: string[] = []
  for (const t of layout.tabs) {
    ids.push(t.id)
    for (const c of t.charts) ids.push(c.id, ...c.axes.map((a) => a.id), ...c.lines.map((l) => l.id))
  }
  for (const id of ids) {
    const n = Number(id.replace(/^\D+/, ''))
    if (Number.isFinite(n) && n > counter) counter = n
  }
}

export function newChart(divisions = DEFAULT_DIVISIONS): Chart {
  return { id: uid('c'), kind: 'time', divisions, axes: [], lines: [] }
}

export function newXYChart(divisions = DEFAULT_DIVISIONS): Chart {
  const c: Chart = { id: uid('c'), kind: 'xy', divisions, axes: [], lines: [], xy: { x: null, xMin: null, xMax: null } }
  addAxis(c, 'left')
  return c
}

export function newTab(name: string): Tab {
  return { id: uid('t'), name, charts: [newChart()] }
}

export function newLayout(): Layout {
  return { tabs: [newTab('Tab 1')] }
}

export function addChart(tab: Tab): Chart | null {
  if (tab.charts.length >= MAX_CHARTS) return null
  const c = newChart()
  tab.charts.push(c)
  return c
}

export function addXYChart(tab: Tab): Chart | null {
  if (tab.charts.length >= MAX_CHARTS) return null
  const c = newXYChart()
  tab.charts.push(c)
  return c
}

export function removeChart(tab: Tab, chartId: string): void {
  tab.charts = tab.charts.filter((c) => c.id !== chartId)
  if (!tab.charts.length) tab.charts.push(newChart())
}

/** Adds a full-height axis on the given side (default: alternate sides). */
export function addAxis(chart: Chart, side?: Side): AxisCfg {
  const s: Side = side ?? (chart.axes.filter((a) => a.side === 'left').length <= chart.axes.filter((a) => a.side === 'right').length ? 'left' : 'right')
  const ax: AxisCfg = { id: uid('a'), side: s, band: [0, chart.divisions], min: null, max: null }
  chart.axes.push(ax)
  return ax
}

export function setDivisions(chart: Chart, divisions: number): void {
  const d = Math.max(2, Math.min(40, Math.round(divisions)))
  for (const ax of chart.axes) {
    ax.band = [Math.min(ax.band[0], d - 1), Math.min(Math.max(ax.band[1], ax.band[0] + 1), d)]
    if (ax.band[1] === chart.divisions) ax.band[1] = d // a full-height band stays full-height
  }
  chart.divisions = d
}

export function setBand(chart: Chart, axisId: string, a: number, b: number): void {
  const ax = chart.axes.find((x) => x.id === axisId)
  if (!ax) return
  a = Math.max(0, Math.min(Math.round(a), chart.divisions - 1))
  b = Math.max(a + 1, Math.min(Math.round(b), chart.divisions))
  ax.band = [a, b]
}

/** Colour (and dash, once the palette is used up) of the next line of the tab (RF-40). */
export function nextStyle(tab: Tab): { color: string; dash: Dash } {
  const lines = tab.charts.flatMap((c) => c.lines)
  return styleFor(lines.length, new Set(lines.map((l) => l.color)))
}

export function addLine(tab: Tab, chart: Chart, fileId: string, channel: string, mode: Mode, axisId?: string): Line {
  const axis = chart.axes.find((a) => a.id === axisId) ?? chart.axes[0] ?? addAxis(chart, 'left')
  const style = nextStyle(tab)
  const line: Line = { id: uid('l'), fileId, channel, mode, axisId: axis.id, color: style.color, width: 1.5, dash: style.dash, marker: false }
  chart.lines.push(line)
  return line
}

export function removeLine(chart: Chart, lineId: string): void {
  chart.lines = chart.lines.filter((l) => l.id !== lineId)
}

export function removeAxis(chart: Chart, axisId: string): void {
  chart.axes = chart.axes.filter((a) => a.id !== axisId)
  const fallback = chart.axes[0]
  chart.lines = fallback ? chart.lines.map((l) => (l.axisId === axisId ? { ...l, axisId: fallback.id } : l)) : chart.lines.filter((l) => l.axisId !== axisId)
}

/** Series (measurement, channel, mode) the tab needs from the server (RI-30: only called for the visible tab). */
export function requiredSeries(tab: Tab): SeriesRef[] {
  const seen = new Set<string>()
  const out: SeriesRef[] = []
  const add = (r: SeriesRef) => {
    const k = seriesKey(r.fileId, r.channel, r.mode)
    if (!seen.has(k)) {
      seen.add(k)
      out.push({ fileId: r.fileId, channel: r.channel, mode: r.mode })
    }
  }
  for (const c of tab.charts) {
    for (const l of c.lines) add(l)
    if (c.xy?.x) add(c.xy.x)
  }
  return out
}

export const seriesKey = (fileId: string, channel: string, mode: Mode) => `${fileId}|${channel}|${mode}`

/** Removes the lines of a deleted calculated channel. */
export function removeCalcFromLayout(layout: Layout, formulaId: string): void {
  for (const t of layout.tabs)
    for (const c of t.charts) {
      c.lines = c.lines.filter((l) => !(isCalc(l) && l.channel === formulaId))
      if (c.xy?.x && c.xy.x.fileId === CALC_FILE && c.xy.x.channel === formulaId) c.xy.x = null
    }
}

/** Closing a measurement removes its lines (and XY x channels that used it). */
export function removeFileFromLayout(layout: Layout, fileId: string): number {
  let n = 0
  for (const t of layout.tabs)
    for (const c of t.charts) {
      const before = c.lines.length
      c.lines = c.lines.filter((l) => l.fileId !== fileId)
      n += before - c.lines.length
      if (c.xy?.x?.fileId === fileId) c.xy.x = null
    }
  return n
}

export function linesUsingFile(layout: Layout, fileId: string): number {
  return layout.tabs.reduce((s, t) => s + t.charts.reduce((q, c) => q + c.lines.filter((l) => l.fileId === fileId).length, 0), 0)
}
