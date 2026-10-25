import { describe, expect, it } from 'vitest'
import { assignLanes, autoRange, axisTicks, bandRange, chartMetrics, niceStep, tabMargins, type AxisCfg } from './axes'
import { formatTicks, isExponential, makeFormat, sigDecimals, stepDecimals } from './format'
import { addAxis, addChart, addLine, newLayout, PALETTE, removeAxis, requiredSeries, setBand, setDivisions } from './layout'

const ax = (id: string, a: number, b: number, side: 'left' | 'right' = 'left', min: number | null = null, max: number | null = null): AxisCfg => ({ id, side, band: [a, b], min, max })

describe('number formatter (RI-27)', () => {
  it('3 significant digits of the max, at least the decimals of the step', () => {
    expect(sigDecimals(1234)).toBe(0)
    expect(sigDecimals(12.34)).toBe(1)
    expect(sigDecimals(0.0123)).toBe(4)
    expect(stepDecimals(0.25)).toBe(2)
    expect(stepDecimals(200)).toBe(0)
    expect(makeFormat(1000, 200).format(400)).toBe('400')
    expect(makeFormat(1.5, 0.25).format(0.5)).toBe('0.50')
  })
  it('exponential at >= 1e6 and below 1e-3', () => {
    expect(isExponential(1e6)).toBe(true)
    expect(isExponential(999999)).toBe(false)
    expect(isExponential(0.0009)).toBe(true)
    expect(isExponential(0)).toBe(false)
    expect(makeFormat(2e6, 5e5).format(2e6)).toBe('2.00e+6')
  })
  it('never prints -0 and keeps ticks distinguishable', () => {
    expect(makeFormat(1, 0.5).format(-0)).toBe('0.0'.replace('0.0', makeFormat(1, 0.5).format(0)))
    const values = [1000000, 1000100, 1000200]
    const labels = formatTicks(values, 1000200, 100)
    expect(new Set(labels).size).toBe(3)
  })
})

describe('band axes (RI-28)', () => {
  it('range formula', () => {
    // 0..100 on divisions 2..6 of 10 -> h=25 -> [-50, 200]
    expect(bandRange(0, 100, 10, 2, 6)).toEqual([-50, 200])
    expect(bandRange(0, 10, 10, 0, 10)).toEqual([0, 10])
  })
  it('nice automatic range spans exactly the divisions', () => {
    const [lo, hi] = autoRange(3.2, 97, 10)
    expect(hi - lo).toBeCloseTo(10 * niceStep(9.38))
    expect(lo).toBeLessThanOrEqual(3.2)
    expect(hi).toBeGreaterThanOrEqual(97)
    expect(autoRange(5, 5, 4)[1]).toBeGreaterThan(5)
  })
  it('ticks only inside the band, with values at the divisions', () => {
    const t = axisTicks(ax('a', 2, 6), 10, 0, 100)
    expect(t.divisions).toEqual([2, 3, 4, 5, 6])
    expect(t.values[0]).toBeLessThanOrEqual(0)
    expect(t.values[4]).toBeGreaterThanOrEqual(100)
    expect(t.scale[0]).toBeCloseTo(t.values[0] - 2 * t.step)
  })
  it('manual min/max are honoured', () => {
    const t = axisTicks(ax('a', 0, 4, 'left', -1, 1), 4, 0, 100)
    expect(t.values).toEqual([-1, -0.5, 0, 0.5, 1])
    expect(t.labels).toEqual(['-1.00', '-0.50', '0.00', '0.50', '1.00'])
  })
})

describe('lanes and margins (RF-13, RI-26)', () => {
  it('overlapping bands on one side use separate lanes, adjacent bands share one', () => {
    const p = assignLanes([ax('a', 0, 10), ax('b', 0, 5), ax('c', 5, 10), ax('d', 0, 10, 'right')])
    expect(p.get('a')!.lane).toBe(0)
    expect(p.get('b')!.lane).toBe(1)
    expect(p.get('c')!.lane).toBe(1)
    expect(p.get('b')!.omitTop).toBe(true) // c starts where b ends
    expect(p.get('c')!.omitTop).toBe(false)
    expect(p.get('d')!.lane).toBe(0)
  })
  it('lane width comes from the formatted labels, sign included; margins are the max over the tab', () => {
    const measure = (s: string) => s.length * 6
    const wide = chartMetrics([ax('a', 0, 4, 'left', -1000, 1000)], 4, () => [0, 1], measure)
    const narrow = chartMetrics([ax('a', 0, 4, 'left', 0, 1)], 4, () => [0, 1], measure)
    expect(wide.margin.left).toBeGreaterThan(narrow.margin.left)
    expect(wide.margin.right).toBe(0)
    expect(tabMargins([wide, narrow]).left).toBe(wide.margin.left)
  })
})

describe('layout model', () => {
  it('limits a tab to 4 charts', () => {
    const tab = newLayout().tabs[0]
    for (let i = 0; i < 5; i++) addChart(tab)
    expect(tab.charts).toHaveLength(4)
  })
  it('adds lines with distinct palette colours, creating the first axis', () => {
    const tab = newLayout().tabs[0]
    const c = tab.charts[0]
    const l1 = addLine(tab, c, 'f1', 'a', 'linear')
    const l2 = addLine(tab, c, 'f1', 'b', 'step')
    expect(c.axes).toHaveLength(1)
    expect(l1.color).toBe(PALETTE[0])
    expect(l2.color).toBe(PALETTE[1])
    expect(requiredSeries(tab)).toEqual([{ fileId: 'f1', channel: 'a', mode: 'linear' }, { fileId: 'f1', channel: 'b', mode: 'step' }])
  })
  it('keeps bands valid when divisions change and when an axis is removed', () => {
    const tab = newLayout().tabs[0]
    const c = tab.charts[0]
    const a1 = addAxis(c, 'left')
    const a2 = addAxis(c, 'right')
    setBand(c, a2.id, 2, 6)
    addLine(tab, c, 'f1', 'x', 'linear', a2.id)
    setDivisions(c, 8)
    expect(a1.band).toEqual([0, 8])
    expect(a2.band).toEqual([2, 6])
    removeAxis(c, a2.id)
    expect(c.lines[0].axisId).toBe(a1.id)
  })
})
