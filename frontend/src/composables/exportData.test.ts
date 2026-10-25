import { describe, expect, it } from 'vitest'
import { buildExportRequest, MissingResult, spreadsheetCsv, tabSeries, type ExportInput } from './exportData'
import { addLine, addXYChart, CALC_FILE, newLayout } from './layout'
import { pdfText, layoutImages } from './pdf'

function input(extra: Partial<ExportInput> = {}): ExportInput {
  const tab = newLayout().tabs[0]
  const c = tab.charts[0]
  addLine(tab, c, 'fa', 'speed', 'linear')
  addLine(tab, c, 'fb', 'speed', 'step')
  addLine(tab, c, 'fa', 'speed', 'linear') // the same series twice: exported once
  const calc = addLine(tab, c, CALC_FILE, 'F1', 'linear')
  calc.title = 'Smooth difference'
  const xy = addXYChart(tab)!
  xy.xy!.x = { fileId: 'fa', channel: 'rpm', mode: 'linear' }
  addLine(tab, xy, 'fa', 'torque', 'average')
  return {
    tab, labelOf: { fa: 'A', fb: 'B' }, multi: true, shifts: { fb: 25 }, handleOf: (id) => (id === 'F1' ? 'h-123' : undefined), formulaName: () => 'x',
    window: null, stepBaseUs: 100_000, format: 'csv', csvLocale: null, filename: 'run', ...extra,
  }
}

describe('export request (RF-35)', () => {
  it('lists each series of the tab once: XY x channels first, then lines', () => {
    expect(tabSeries(input().tab).map((r) => `${r.fileId}:${r.channel}:${r.mode}`)).toEqual(['fa:speed:linear', 'fb:speed:step', '~calc:F1:linear', 'fa:rpm:linear', 'fa:torque:average'])
  })
  it('labels channels with their measurement when several are open, calculated channels with ƒ and their handle', () => {
    const r = buildExportRequest(input())
    expect(r.series.map((s) => s.label)).toEqual(['A: speed', 'B: speed', 'ƒ Smooth difference', 'A: rpm', 'A: torque'])
    expect(r.series[2]).toEqual({ label: 'ƒ Smooth difference', handle: 'h-123' })
    expect(r.series[1]).toEqual({ label: 'B: speed', file: 'fb', channel: 'speed', mode: 'step' })
    expect(r.shifts).toEqual({ fb: 25 })
    expect(buildExportRequest(input({ multi: false })).series[0].label).toBe('speed')
  })
  it('the visible window becomes inclusive integer steps; no zoom exports everything', () => {
    const r = buildExportRequest(input({ window: [10.04, 20.06] }))
    expect([r.k_from, r.k_to]).toEqual([101, 200])
    const all = buildExportRequest(input())
    expect([all.k_from, all.k_to]).toEqual([null, null])
  })
  it('a calculated channel without a result stops the export with a clear error', () => {
    expect(() => buildExportRequest(input({ handleOf: () => undefined }))).toThrow(MissingResult)
  })
  it('csv locale options only apply to csv', () => {
    expect(buildExportRequest(input({ csvLocale: { decimal: ',', delimiter: ';' } })).csv).toEqual({ decimal: ',', delimiter: ';' })
    expect(buildExportRequest(input({ format: 'parquet', csvLocale: { decimal: ',', delimiter: ';' } })).csv).toBeUndefined()
  })
  it('spreadsheet CSV follows the decimal separator of the language', () => {
    expect(spreadsheetCsv(',')).toEqual({ decimal: ',', delimiter: ';' })
    expect(spreadsheetCsv('.')).toEqual({ decimal: '.', delimiter: ',' })
  })
})

describe('pdf helpers (RF-36)', () => {
  it('replaces the characters the built-in PDF fonts cannot show', () => {
    expect(pdfText('ƒ Δ → — ⚠ 1,5')).toBe('f Delta -> - ! 1,5')
    expect(pdfText('日本')).toBe('??')
  })
  it('scales the graphs to share the page without changing their aspect ratio', () => {
    const [a, b] = layoutImages([{ w: 800, h: 400 }, { w: 800, h: 400 }], 100, 277)
    expect(a.h + b.h + 3).toBeCloseTo(100)
    expect(a.w / a.h).toBeCloseTo(2)
    expect(layoutImages([{ w: 800, h: 100 }], 200, 277)[0]).toEqual({ w: 277, h: 34.625 })        // already fits: not enlarged beyond the width
    expect(layoutImages([], 100, 277)).toEqual([])
  })
})
