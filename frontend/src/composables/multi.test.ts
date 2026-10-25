import { describe, expect, it } from 'vitest'
import { addCursor, clampK, moveCursor, newCursorState } from './cursors'
import { gridSeconds } from './grid'
import { addLine, addXYChart, linesUsingFile, newLayout, removeFileFromLayout, requiredSeries } from './layout'
import { closeMeasure, extentK, makePrimary, openMeasure, setShiftSeconds, shiftSeconds, t0OnCursor, type OpenMeasure } from './measures'
import { bandStats, valueAt, type GridSeries } from './readout'
import { restore, serialize, type SessionState } from './session'
import { pointIndexAt, segmentRange, xyPoints, type XYSeries } from './xy'

const STEP = 100_000
const fresh = () => {
  const l: OpenMeasure[] = []
  openMeasure(l, 'a', 'A.bin')
  openMeasure(l, 'b', 'B.bin')
  l[0].durationUs = 100_000_000 // 1000 steps
  l[1].durationUs = 50_000_000 // 500 steps
  return l
}

describe('open measurements (RF-01…05)', () => {
  it('assigns labels A–D, at most four, labels freed on close and reused', () => {
    const l: OpenMeasure[] = []
    ;['a', 'b', 'c', 'd'].forEach((id) => openMeasure(l, id, id))
    expect(l.map((m) => m.label)).toEqual(['A', 'B', 'C', 'D'])
    expect(openMeasure(l, 'e', 'e')).toBeNull()
    expect(openMeasure(l, 'a', 'a')).toBeNull()
    closeMeasure(l, 'b')
    expect(openMeasure(l, 'e', 'e')!.label).toBe('B')
  })
  it('the primary has no shift', () => {
    const l: OpenMeasure[] = []
    expect(openMeasure(l, 'a', 'a', 77)!.shiftK).toBe(0)
    expect(openMeasure(l, 'b', 'b', 77)!.shiftK).toBe(77)
  })
  it('shift in seconds is rounded to 0.1 s and may be negative; no drift', () => {
    const l = fresh()
    setShiftSeconds(l[1], 2.34, STEP)
    expect(l[1].shiftK).toBe(23)
    expect(shiftSeconds(l[1], STEP)).toBeCloseTo(2.3)
    setShiftSeconds(l[1], -1.26, STEP)
    expect(l[1].shiftK).toBe(-13)
    for (let i = 0; i < 100_000; i++) setShiftSeconds(l[1], 0.1 * i, STEP)
    expect(Number.isInteger(l[1].shiftK)).toBe(true)
    expect(l[1].shiftK).toBe(99_999)
  })
  it('shared extent covers negative times (RF-02, RF-03)', () => {
    const l = fresh()
    expect(extentK(l, STEP)).toEqual({ min: 0, max: 1000 })
    l[1].shiftK = -200
    expect(extentK(l, STEP)).toEqual({ min: -200, max: 1000 })
    l[1].shiftK = 800
    expect(extentK(l, STEP)).toEqual({ min: 0, max: 1300 })
  })
  it('t0 on cursor moves the selected non-primary measurement to the cursor', () => {
    const l = fresh()
    expect(t0OnCursor(l, 'b', 345)).toBe(true)
    expect(l[1].shiftK).toBe(345)
    expect(t0OnCursor(l, 'a', 10)).toBe(false) // the primary stays at 0
    expect(l[0].shiftK).toBe(0)
  })
  it('making another measurement primary re-bases the shifts and reports the delta for cursors', () => {
    const l = fresh()
    l[1].shiftK = 50
    const delta = makePrimary(l, 'b')
    expect(delta).toBe(50)
    expect(l.map((m) => [m.id, m.shiftK])).toEqual([['b', 0], ['a', -50]])
  })
})

describe('shift in time axis, readings and cursors', () => {
  it('x coordinates use exact integer indices before converting to seconds', () => {
    expect(Array.from(gridSeconds(0, 10, 3, STEP, 5))).toEqual([0.5, 1.5, 2.5])
    expect(Array.from(gridSeconds(0, 1, 3, STEP, -2))).toEqual([-0.2, -0.1, 0])
  })
  const s: GridSeries = { n: 1, jStart: 0, values: Float64Array.from([10, 20, 30, 40, 50]) } // own k 0..4
  it('reads through the shift and reports out of measure before and after (RF-05)', () => {
    const p = { shiftK: 10, durK: 4 }
    expect(valueAt(s, 12, p)).toMatchObject({ value: 30 })
    expect(valueAt(s, 9, p)).toEqual({ value: null, onGrid: false, out: true })
    expect(valueAt(s, 15, p)).toEqual({ value: null, onGrid: false, out: true })
    expect(valueAt(s, 14, p).value).toBe(50)
    expect(valueAt(s, 0, { shiftK: -3, durK: 4 }).value).toBe(40) // own k = 3
  })
  it('band statistics are computed in the measurement own time', () => {
    const st = bandStats(s, 11, 13, STEP, { shiftK: 10, durK: 4 })
    expect([st.min, st.max, st.points, st.holes]).toEqual([20, 40, 3, 0])
    const wide = bandStats(s, 5, 30, STEP, { shiftK: 10, durK: 4 })
    expect(wide.points).toBe(5)
    expect(wide.holes).toBe(0) // time outside the measurement is not a gap
  })
  it('cursors can sit at negative times', () => {
    const st = newCursorState()
    const c = addCursor(st, 'single', -50, { min: -200, max: 1000 })
    expect(c.k).toBe(-50)
    moveCursor(c, -500, { min: -200, max: 1000 })
    expect(c.k).toBe(-200)
    expect(clampK(5000, { min: -200, max: 1000 })).toBe(1000)
    expect(clampK(-5, 100)).toBe(0)
  })
})

describe('XY points (RF-18)', () => {
  const mk = (n: number, values: number[], shiftK = 0, jStart = 0): XYSeries => ({ n, jStart, values: Float64Array.from(values), shiftK })
  it('same grid: pairs index by index, holes drop the point', () => {
    const p = xyPoints(mk(1, [1, 2, NaN, 4]), mk(1, [10, NaN, 30, 40]))
    expect(p).toEqual({ k: [0, 3], x: [1, 4], y: [10, 40] })
  })
  it('different grids: the faster one is sub-sampled by index (measured values only)', () => {
    const fast = mk(1, Array.from({ length: 30 }, (_, i) => i)) // 0..29
    const slow = mk(10, [100, 200, 300]) // k = 0, 10, 20
    const p = xyPoints(fast, slow)
    expect(p.k).toEqual([0, 10, 20])
    expect(p.x).toEqual([0, 10, 20])
    expect(p.y).toEqual([100, 200, 300])
    expect(xyPoints(slow, fast).x).toEqual([100, 200, 300]) // symmetric in the roles
  })
  it('shifted measurements only meet where their grids coincide', () => {
    const a = mk(1, Array.from({ length: 20 }, (_, i) => i))
    const b = mk(1, Array.from({ length: 20 }, (_, i) => 100 + i), 5)
    const p = xyPoints(a, b)
    expect(p.k[0]).toBe(5)
    expect(p.x[0]).toBe(5)
    expect(p.y[0]).toBe(100)
    expect(p.k.length).toBe(15)
    // two 1 Hz grids whose offset is not a multiple of 10 steps never coincide
    expect(xyPoints(mk(10, [1, 2, 3]), mk(10, [1, 2, 3], 5)).k).toEqual([])
  })
  it('cursor point and band segment', () => {
    const p = { k: [0, 10, 20, 30], x: [0, 1, 2, 3], y: [0, 1, 2, 3] }
    expect(pointIndexAt(p, 15)).toBe(1)
    expect(pointIndexAt(p, -1)).toBe(-1)
    expect(pointIndexAt(p, 99)).toBe(3)
    expect(segmentRange(p, 5, 25)).toEqual([1, 2])
    expect(segmentRange(p, 11, 19)).toBeNull()
    expect(segmentRange(p, 0, 30)).toEqual([0, 3])
  })
})

describe('layout with several measurements and XY charts', () => {
  it('collects series per measurement, including the XY x channel, and removes a closed measurement', () => {
    const lay = newLayout()
    const tab = lay.tabs[0]
    addLine(tab, tab.charts[0], 'm1', 'a', 'linear')
    addLine(tab, tab.charts[0], 'm2', 'a', 'linear')
    const xy = addXYChart(tab)!
    xy.xy!.x = { fileId: 'm1', channel: 'x', mode: 'linear' }
    addLine(tab, xy, 'm2', 'y', 'step')
    expect(requiredSeries(tab).map((r) => `${r.fileId}:${r.channel}:${r.mode}`)).toEqual(['m1:a:linear', 'm2:a:linear', 'm2:y:step', 'm1:x:linear'])
    expect(linesUsingFile(lay, 'm2')).toBe(2)
    expect(removeFileFromLayout(lay, 'm1')).toBe(1)
    expect(xy.xy!.x).toBeNull()
  })
  it('session snapshot with two measurements and an XY chart round-trips; schema 1 is rejected', () => {
    const layout = newLayout()
    const tab = layout.tabs[0]
    const xy = addXYChart(tab)!
    xy.xy!.x = { fileId: 'a', channel: 'x', mode: 'linear' }
    addLine(tab, xy, 'b', 'y', 'linear')
    const cursors = newCursorState()
    addCursor(cursors, 'single', -4, { min: -10, max: 100 })
    const s: SessionState = {
      schema: 3, measurements: [{ id: 'a', shiftK: 0 }, { id: 'b', shiftK: -12 }], layout, formulas: [], aliases: [], activeTabId: tab.id, cursors, xWindows: {}, acceptedMarkers: [], selectedMeasureId: 'b',
    }
    expect(restore(serialize(s))).toEqual(s)
    expect(restore(JSON.stringify({ ...s, schema: 1 }))).toBeNull()
    expect(restore(JSON.stringify({ ...s, measurements: [{ id: 'a', shiftK: 0 }, { id: 'a', shiftK: 1 }] }))).toBeNull()
    const bad = JSON.parse(serialize(s))
    delete bad.layout.tabs[0].charts[1].xy
    expect(restore(JSON.stringify(bad))).toBeNull()
  })
})
