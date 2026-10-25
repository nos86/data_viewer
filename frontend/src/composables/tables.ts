/** The cursor table and band statistics as plain data, shared by the screen components and the PDF (RF-26, RF-27, RF-36). */
import type { ChannelOut, SeriesData } from '../api/client'
import { channelFormat } from './format'
import { lineName, lineTag, seriesKey, type Line } from './layout'
import type { Cursor } from './cursors'
import { bandStats, valueAt, type Placement } from './readout'

export interface TableContext {
  series: Map<string, SeriesData>
  channels: Map<string, ChannelOut> // key: fileId|channel
  placements: Record<string, Placement>
  labels: Record<string, string> | null
  stepBaseUs: number
}

/** One row per distinct channel + mode, in the order of the lines. */
export function distinctLines(lines: Line[]): Line[] {
  const seen = new Set<string>()
  return lines.filter((l) => {
    const k = seriesKey(l.fileId, l.channel, l.mode)
    if (seen.has(k)) return false
    seen.add(k)
    return true
  })
}

export interface CursorColumn {
  cursor: Cursor
  at: 'k' | 'k2'
  label: string
}

/** A band has a column for its start and one for its end. */
export function cursorColumns(cursors: Cursor[]): CursorColumn[] {
  return cursors.flatMap((c) =>
    c.kind === 'band'
      ? [
          { cursor: c, at: 'k' as const, label: `${c.number} start` },
          { cursor: c, at: 'k2' as const, label: `${c.number} end` },
        ]
      : [{ cursor: c, at: 'k' as const, label: String(c.number) }],
  )
}

export interface Cell {
  text: string
  off: boolean // between grid points of that channel, or out of measure
}

export function cursorCell(l: Line, col: CursorColumn, ctx: TableContext): Cell {
  const s = ctx.series.get(seriesKey(l.fileId, l.channel, l.mode))
  if (!s) return { text: '…', off: false }
  const r = valueAt({ n: s.n, jStart: s.jStart, values: s.values }, col.cursor[col.at], ctx.placements[l.fileId])
  const info = ctx.channels.get(`${l.fileId}|${l.channel}`)
  if (r.out) return { text: 'out of measure', off: true } // RF-05: the measurement is not running here
  return { text: r.value == null ? '—' : channelFormat(info?.v_min, info?.v_max).format(r.value), off: !r.onGrid }
}

export interface TableRow {
  line: Line
  name: string
  tag: string | null | undefined
  cells: Cell[]
}

export function cursorTable(lines: Line[], cursors: Cursor[], ctx: TableContext): { columns: CursorColumn[]; rows: TableRow[] } {
  const columns = cursorColumns(cursors)
  const rows = distinctLines(lines).map((l) => ({ line: l, name: lineName(l), tag: lineTag(l, ctx.labels), cells: columns.map((c) => cursorCell(l, c, ctx)) }))
  return { columns, rows }
}

export const STAT_HEADERS = ['Min', 'Max', 'Mean', 'Start', 'End', 'Δ'] as const

export interface StatRow {
  line: Line
  name: string
  tag: string | null | undefined
  loaded: boolean
  values: string[] // one text per STAT_HEADERS
  holes: number
  points: number
}

export function bandTable(band: Cursor | null, lines: Line[], ctx: TableContext): StatRow[] {
  if (!band || band.kind !== 'band') return []
  return distinctLines(lines).map((l) => {
    const s = ctx.series.get(seriesKey(l.fileId, l.channel, l.mode))
    const info = ctx.channels.get(`${l.fileId}|${l.channel}`)
    const f = channelFormat(info?.v_min, info?.v_max)
    const fmt = (v: number | null | undefined) => (v == null ? '—' : f.format(v))
    const st = s ? bandStats({ n: s.n, jStart: s.jStart, values: s.values }, band.k, band.k2, ctx.stepBaseUs, ctx.placements[l.fileId]) : null
    return {
      line: l,
      name: lineName(l),
      tag: lineTag(l, ctx.labels),
      loaded: !!s,
      values: st ? [st.min, st.max, st.mean, st.start, st.end, st.delta].map(fmt) : STAT_HEADERS.map(() => '…'),
      holes: st?.holes ?? 0,
      points: st?.points ?? 0,
    }
  })
}
