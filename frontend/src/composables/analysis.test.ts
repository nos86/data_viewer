import { describe, expect, it } from 'vitest'
import {
  activeCursor, addCursor, centreWindow, clampK, cycleActive, moveCursor, newCursorState, outOfView, removeCursor, setEdge, snapK, kToSeconds,
} from './cursors'
import { keyAction, type KeyLike } from './keys'
import { addLine, newLayout, syncUid, uid } from './layout'
import { bandStats, valueAt, type GridSeries } from './readout'
import { restore, serialize, type SessionState } from './session'

const STEP = 100_000
const key = (key: string, extra: Partial<KeyLike> = {}): KeyLike => ({ key, shiftKey: false, altKey: false, ctrlKey: false, metaKey: false, target: { tagName: 'DIV' }, ...extra })

describe('cursor model (integer lattice, RI-25)', () => {
  it('snaps seconds to k and back without drift', () => {
    expect(snapK(1.23, STEP)).toBe(12)
    expect(snapK(0.049, STEP)).toBe(0)
    expect(snapK(0.05, STEP)).toBe(1)
    const st = newCursorState()
    const c = addCursor(st, 'single', 0, 108000)
    for (let i = 0; i < 1_000_000; i++) moveCursor(c, 1, 5_000_000)
    expect(c.k).toBe(1_000_000)
    expect(Number.isInteger(c.k)).toBe(true)
    expect(kToSeconds(c.k, STEP)).toBe(100000)
  })
  it('clamps to [0, kMax]', () => {
    const st = newCursorState()
    const c = addCursor(st, 'single', 5, 100)
    moveCursor(c, -50, 100)
    expect(c.k).toBe(0)
    moveCursor(c, 500, 100)
    expect(c.k).toBe(100)
    expect(clampK(-3, 10)).toBe(0)
  })
  it('numbers are never reused and the active cursor moves on after removal', () => {
    const st = newCursorState()
    const a = addCursor(st, 'single', 1, 100)
    const b = addCursor(st, 'single', 2, 100)
    removeCursor(st, b.id)
    const c = addCursor(st, 'single', 3, 100)
    expect([a.number, b.number, c.number]).toEqual([1, 2, 3])
    removeCursor(st, c.id)
    expect(activeCursor(st)?.id).toBe(a.id)
    removeCursor(st, a.id)
    expect(st.activeId).toBeNull()
    cycleActive(st, 1) // no-op on empty
  })
  it('cycles through cursors', () => {
    const st = newCursorState()
    const a = addCursor(st, 'single', 1, 100)
    const b = addCursor(st, 'single', 2, 100)
    cycleActive(st, 1)
    expect(st.activeId).toBe(a.id)
    cycleActive(st, -1)
    expect(st.activeId).toBe(b.id)
  })
  it('band: whole move keeps width, edges resize and swap when crossed', () => {
    const st = newCursorState()
    const b = addCursor(st, 'band', 10, 100, 20)
    expect([b.k, b.k2]).toEqual([10, 30])
    moveCursor(b, 5, 100)
    expect([b.k, b.k2]).toEqual([15, 35])
    moveCursor(b, 1000, 100)
    expect([b.k, b.k2]).toEqual([80, 100])
    moveCursor(b, -3, 100, 'end')
    expect(b.k2).toBe(97)
    setEdge(b, 'end', 50, 100)
    expect([b.k, b.k2]).toEqual([50, 80])
  })
  it('detects cursors outside the view and recentres keeping the span', () => {
    const st = newCursorState()
    const c = addCursor(st, 'single', 500, 10_000) // 50 s
    expect(outOfView(c, [0, 10], STEP)).toBe('right')
    expect(outOfView(c, [100, 200], STEP)).toBe('left')
    expect(outOfView(c, [40, 60], STEP)).toBeNull()
    expect(centreWindow([0, 10], 50, 1000)).toEqual([45, 55])
    expect(centreWindow([0, 10], 2, 1000)).toEqual([0, 10])
    expect(centreWindow([990, 1000], 1000, 1000)).toEqual([990, 1000])
  })
})

describe('readings and band statistics (RF-26, RF-27)', () => {
  const s10: GridSeries = { n: 10, jStart: 0, values: Float64Array.from([1, 2, NaN, 4, 5]) } // 1 Hz grid, k = 0,10,20,...
  const s1: GridSeries = { n: 1, jStart: 0, values: Float64Array.from([0, 10, 20, 30, 40, NaN, 60, 70, 80, 90]) }

  it('reads the last grid point at or before k and flags off-grid cursors', () => {
    expect(valueAt(s10, 10)).toEqual({ value: 2, onGrid: true })
    expect(valueAt(s10, 17)).toEqual({ value: 2, onGrid: false })
    expect(valueAt(s10, 25)).toEqual({ value: null, onGrid: false }) // hole
    expect(valueAt(s10, 999)).toEqual({ value: null, onGrid: false }) // after the channel/measurement end (RF-05)
    expect(valueAt(s1, 3).value).toBe(30)
  })
  it('honours j_start of a windowed series', () => {
    const w: GridSeries = { n: 1, jStart: 5, values: Float64Array.from([5, 6, 7]) }
    expect(valueAt(w, 4).value).toBeNull()
    expect(valueAt(w, 6).value).toBe(6)
  })
  it('computes band statistics on grid points, with hole count', () => {
    const st = bandStats(s1, 2, 7, STEP) // points 2..7 -> 20,30,40,NaN,60,70
    expect(st).toMatchObject({ min: 20, max: 70, points: 6, holes: 1, start: 20, end: 70, delta: 50 })
    expect(st.mean).toBeCloseTo((20 + 30 + 40 + 60 + 70) / 5)
    expect(st.durationS).toBeCloseTo(0.5)
  })
  it('bands on a coarse grid only count its own points', () => {
    const st = bandStats(s10, 5, 35, STEP) // grid points at k=10,20,30
    expect(st.points).toBe(3)
    expect(st.holes).toBe(1)
    expect([st.min, st.max]).toEqual([2, 4])
    expect(st.start).toBe(1) // held from k=0
    expect(st.end).toBe(4) // k=35 -> j=3
  })
  it('all-hole band has null statistics', () => {
    const st = bandStats({ n: 1, jStart: 0, values: Float64Array.from([NaN, NaN]) }, 0, 1, STEP)
    expect(st.min).toBeNull()
    expect(st.holes).toBe(2)
    expect(st.delta).toBeNull()
  })
})

describe('keyboard (RF-39)', () => {
  it('arrow keys move by one step, Shift by ten, Alt moves the band end', () => {
    expect(keyAction(key('ArrowRight'))).toEqual({ type: 'moveCursor', dk: 1, edge: 'whole' })
    expect(keyAction(key('ArrowLeft', { shiftKey: true }))).toEqual({ type: 'moveCursor', dk: -10, edge: 'whole' })
    expect(keyAction(key('ArrowRight', { altKey: true }))).toEqual({ type: 'moveCursor', dk: 1, edge: 'end' })
  })
  it('zoom, tab and cursor shortcuts', () => {
    expect(keyAction(key('+'))).toEqual({ type: 'zoom', factor: 0.5 })
    expect(keyAction(key('-'))).toEqual({ type: 'zoom', factor: 2 })
    expect(keyAction(key('0'))).toEqual({ type: 'resetZoom' })
    expect(keyAction(key('a'))).toEqual({ type: 'applyZoomAll' })
    expect(keyAction(key('PageDown'))).toEqual({ type: 'tab', delta: 1 })
    expect(keyAction(key('3', { altKey: true, code: 'Digit3' }))).toEqual({ type: 'tabIndex', index: 2 })
    expect(keyAction(key('n'))?.type).toBe('newCursor')
    expect(keyAction(key('b'))?.type).toBe('newBand')
    expect(keyAction(key('Delete'))?.type).toBe('removeCursor')
    expect(keyAction(key(']'))).toEqual({ type: 'cycleCursor', dir: 1 })
    expect(keyAction(key('g'))?.type).toBe('goToCursor')
  })
  it('ignores typing in form fields and browser shortcuts', () => {
    expect(keyAction(key('ArrowRight', { target: { tagName: 'INPUT' } }))).toBeNull()
    expect(keyAction(key('n', { target: { tagName: 'TEXTAREA' } }))).toBeNull()
    expect(keyAction(key('a', { ctrlKey: true }))).toBeNull()
    expect(keyAction(key('x'))).toBeNull()
  })
})

describe('session snapshot (RI-32)', () => {
  function state(): SessionState {
    const layout = newLayout()
    const tab = layout.tabs[0]
    addLine(tab, tab.charts[0], 'f1', 'f10_000', 'linear')
    const cursors = newCursorState()
    addCursor(cursors, 'single', 12, 1000)
    addCursor(cursors, 'band', 20, 1000, 15)
    return { schema: 3, measurements: [{ id: 'abc', shiftK: 0 }, { id: 'def', shiftK: -25 }], layout, formulas: [], aliases: [], activeTabId: tab.id, cursors, xWindows: { [tab.id]: [1, 5], other: null }, acceptedMarkers: ['l9'], selectedMeasureId: 'def' }
  }
  it('round-trips', () => {
    const s = state()
    expect(restore(serialize(s))).toEqual(s)
  })
  it('ignores corrupt, foreign or older snapshots', () => {
    expect(restore(null)).toBeNull()
    expect(restore('{not json')).toBeNull()
    expect(restore(JSON.stringify({ ...state(), schema: 0 }))).toBeNull()
    expect(restore(JSON.stringify({ ...state(), layout: { tabs: [] } }))).toBeNull()
    const bad = state()
    bad.layout.tabs[0].charts[0].lines[0].axisId = 'missing'
    expect(restore(serialize(bad))).toBeNull()
    const badCursor = state()
    badCursor.cursors.items[0].k = 1.5
    expect(restore(serialize(badCursor))).toBeNull()
  })
  it('repairs recoverable fields', () => {
    const s = state()
    const raw = JSON.parse(serialize(s))
    raw.activeTabId = 'gone'
    raw.xWindows = { [s.layout.tabs[0].id]: [5, 1], x: 'nope' }
    raw.cursors.activeId = 'gone'
    const r = restore(JSON.stringify(raw))!
    expect(r.activeTabId).toBe(s.layout.tabs[0].id)
    expect(r.xWindows).toEqual({})
    expect(r.cursors.activeId).toBeNull()
  })
  it('new ids continue after the restored ones', () => {
    const r = restore(serialize(state()))!
    syncUid(r.layout)
    const ids = new Set(r.layout.tabs.flatMap((t) => [t.id, ...t.charts.flatMap((c) => [c.id, ...c.axes.map((a) => a.id), ...c.lines.map((l) => l.id)])]))
    expect(ids.has(uid('x'))).toBe(false)
  })
})
