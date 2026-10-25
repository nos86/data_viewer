/** Builds the request for POST /export from what the current tab shows (RF-35). Pure: no network, no DOM. */
import type { ExportRequest } from '../api/client'
import { CALC_FILE, lineName, seriesKey, type Chart, type Mode, type Tab } from './layout'

export interface ExportInput {
  tab: Tab
  labelOf: Record<string, string> // open measurement id -> A/B/...
  multi: boolean // several measurements open: prefix the channel with its label
  shifts: Record<string, number>
  handleOf: (formulaId: string) => string | undefined // result handle of a calculated channel
  formulaName: (formulaId: string) => string
  window: [number, number] | null // seconds, the tab's zoom; null = everything
  stepBaseUs: number
  format: 'csv' | 'parquet'
  csvLocale: { decimal: '.' | ','; delimiter: ',' | ';' } | null
  filename: string
}

export class MissingResult extends Error {
  constructor(public formulaId: string) {
    super('A calculated channel has no result yet')
  }
}

type Ref = { fileId: string; channel: string; mode: Mode }

/** Every distinct series on the tab's graphs, in order: lines of time and XY graphs, and the X channels of XY graphs. */
export function tabSeries(tab: Tab): Ref[] {
  const out: Ref[] = []
  const seen = new Set<string>()
  const add = (r: Ref) => {
    const k = seriesKey(r.fileId, r.channel, r.mode)
    if (!seen.has(k)) {
      seen.add(k)
      out.push(r)
    }
  }
  tab.charts.forEach((c: Chart) => {
    if (c.xy?.x) add(c.xy.x)
    c.lines.forEach((l) => add(l))
  })
  return out
}

export function buildExportRequest(i: ExportInput): ExportRequest {
  const titles = new Map(i.tab.charts.flatMap((c) => c.lines).map((l) => [`${l.fileId}|${l.channel}`, lineName(l)]))
  const series: ExportRequest['series'] = tabSeries(i.tab).map((r) => {
    if (r.fileId === CALC_FILE) {
      const handle = i.handleOf(r.channel)
      if (!handle) throw new MissingResult(r.channel)
      return { label: 'ƒ ' + (titles.get(`${r.fileId}|${r.channel}`) ?? i.formulaName(r.channel)), handle }
    }
    return { label: (i.multi ? `${i.labelOf[r.fileId] ?? '?'}: ` : '') + r.channel, file: r.fileId, channel: r.channel, mode: r.mode }
  })
  const w = i.window
  return {
    format: i.format,
    series,
    // first and last whole step inside the window (inclusive)
    k_from: w ? Math.ceil((w[0] * 1e6) / i.stepBaseUs - 1e-9) : null,
    k_to: w ? Math.floor((w[1] * 1e6) / i.stepBaseUs + 1e-9) : null,
    shifts: i.shifts,
    csv: i.format === 'csv' && i.csvLocale ? i.csvLocale : undefined,
    filename: i.filename,
  }
}

/** Spreadsheet-friendly CSV: the decimal separator of the browser language, and ';' as delimiter when that is a comma. */
export function spreadsheetCsv(decimal: string): { decimal: '.' | ','; delimiter: ',' | ';' } {
  return decimal === ',' ? { decimal: ',', delimiter: ';' } : { decimal: '.', delimiter: ',' }
}
