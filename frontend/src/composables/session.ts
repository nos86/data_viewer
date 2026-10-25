/** Session state in sessionStorage (RI-32): survives a refresh, disappears when the tab is closed.
 * Not a template file (step 5) and never sent to the backend. */
import type { CursorState } from './cursors'
import type { Formula } from './formula'
import type { Layout } from './layout'
import type { Alias } from './template'

export const SESSION_KEY = 'viewer.session'
export const SCHEMA = 3

export interface SessionState {
  schema: typeof SCHEMA
  measurements: { id: string; shiftK: number }[] // open measurements, primary first (RF-01, RF-03)
  layout: Layout
  formulas: Formula[] // calculated channels (RF-28)
  aliases: Alias[] // replacements the user asked to remember (RF-31)
  activeTabId: string
  cursors: CursorState
  xWindows: Record<string, [number, number] | null>
  acceptedMarkers: string[]
  selectedMeasureId: string | null
}

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const isStr = (v: unknown): v is string => typeof v === 'string'
const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)
const isInt = (v: unknown): v is number => isNum(v) && Number.isInteger(v)

function validRef(r: unknown): boolean {
  return isObj(r) && isStr(r.fileId) && isStr(r.channel) && ['step', 'linear', 'average'].includes(r.mode as string)
}

function validXY(c: Record<string, unknown>): boolean {
  if (c.kind === 'time') return c.xy === undefined
  const xy = c.xy
  return isObj(xy) && (xy.x === null || validRef(xy.x)) && (xy.xMin === null || isNum(xy.xMin)) && (xy.xMax === null || isNum(xy.xMax))
}

function validBlock(b: unknown): boolean {
  if (!isObj(b) || !isStr(b.id)) return false
  switch (b.type) {
    case 'ch':
      return isStr(b.slot) && isStr(b.channel) && ['step', 'linear', 'average'].includes(b.mode as string)
    case 'const':
      return isNum(b.value)
    case 'op':
      return isStr(b.op) && isInt(b.version) && isObj(b.params) && Array.isArray(b.inputs) && b.inputs.every((i) => i === null || isStr(i))
    case 'calc':
      return isStr(b.formulaId)
  }
  return false
}

export function validFormulas(fs: unknown): fs is Formula[] {
  return (
    Array.isArray(fs) &&
    fs.every(
      (f) =>
        isObj(f) && isStr(f.id) && isStr(f.name) && Array.isArray(f.blocks) && f.blocks.every(validBlock) && (f.output === null || isStr(f.output)) &&
        isObj(f.positions) && Object.values(f.positions).every((p) => isObj(p) && isNum(p.x) && isNum(p.y)),
    ) &&
    new Set(fs.map((f) => (f as { id: string }).id)).size === fs.length
  )
}

export function validLayout(l: unknown): l is Layout {
  if (!isObj(l) || !Array.isArray(l.tabs) || !l.tabs.length) return false
  return l.tabs.every(
    (t) =>
      isObj(t) && isStr(t.id) && isStr(t.name) && Array.isArray(t.charts) && t.charts.length > 0 && t.charts.length <= 4 &&
      t.charts.every(
        (c) =>
          isObj(c) && isStr(c.id) && (c.kind === 'time' || c.kind === 'xy') && isInt(c.divisions) && Array.isArray(c.axes) && Array.isArray(c.lines) && validXY(c) &&
          c.axes.every((a) => isObj(a) && isStr(a.id) && (a.side === 'left' || a.side === 'right') && Array.isArray(a.band) && a.band.length === 2 && a.band.every(isInt) && (a.min === null || isNum(a.min)) && (a.max === null || isNum(a.max))) &&
          (c.lines as Record<string, unknown>[]).every(
            (x) =>
              isObj(x) && isStr(x.id) && isStr(x.fileId) && isStr(x.channel) && ['step', 'linear', 'average'].includes(x.mode as string) && isStr(x.axisId) &&
              (c.axes as Record<string, unknown>[]).some((a) => a.id === x.axisId) && isStr(x.color) && isNum(x.width) && ['solid', 'dashed', 'dotted'].includes(x.dash as string) && typeof x.marker === 'boolean',
          ),
      ),
  )
}

function validMeasurements(m: unknown): boolean {
  return Array.isArray(m) && m.length <= 4 && m.every((x) => isObj(x) && isStr(x.id) && isInt(x.shiftK)) && new Set(m.map((x) => (x as { id: string }).id)).size === m.length
}

function validCursors(c: unknown): c is CursorState {
  return (
    isObj(c) && isInt(c.nextNumber) && Array.isArray(c.items) && (c.activeId === null || isStr(c.activeId)) &&
    c.items.every((x) => isObj(x) && isStr(x.id) && isInt(x.number) && (x.kind === 'single' || x.kind === 'band') && isInt(x.k) && isInt(x.k2) && x.k2 >= x.k && (x.color === undefined || isStr(x.color)))
  )
}

export const serialize = (s: SessionState): string => JSON.stringify(s)

/** Parses a stored snapshot; anything unexpected (corrupt, other schema) yields null and the app starts fresh. */
export function restore(raw: string | null): SessionState | null {
  if (!raw) return null
  try {
    const o = JSON.parse(raw)
    if (!isObj(o) || o.schema !== SCHEMA) return null
    if (!validMeasurements(o.measurements) || !validLayout(o.layout) || !validCursors(o.cursors) || !validFormulas(o.formulas)) return null
    const layout = o.layout as Layout
    const activeTabId = isStr(o.activeTabId) && layout.tabs.some((t) => t.id === o.activeTabId) ? o.activeTabId : layout.tabs[0].id
    const xWindows: SessionState['xWindows'] = {}
    if (isObj(o.xWindows)) {
      for (const [k, v] of Object.entries(o.xWindows)) {
        if (v === null) xWindows[k] = null
        else if (Array.isArray(v) && v.length === 2 && isNum(v[0]) && isNum(v[1]) && v[1] > v[0]) xWindows[k] = [v[0], v[1]]
      }
    }
    const cursors = o.cursors as CursorState
    if (cursors.activeId && !cursors.items.some((c) => c.id === cursors.activeId)) cursors.activeId = null
    return {
      schema: SCHEMA,
      measurements: o.measurements as SessionState['measurements'],
      layout,
      formulas: o.formulas as Formula[],
      aliases: Array.isArray(o.aliases) ? (o.aliases as Alias[]).filter((a) => isObj(a) && isObj(a.from) && isObj(a.to)) : [],
      activeTabId,
      cursors,
      xWindows,
      acceptedMarkers: Array.isArray(o.acceptedMarkers) ? o.acceptedMarkers.filter(isStr) : [],
      selectedMeasureId: isStr(o.selectedMeasureId) && (o.measurements as { id: string }[]).some((m) => m.id === o.selectedMeasureId) ? o.selectedMeasureId : null,
    }
  } catch {
    return null
  }
}

/** sessionStorage can be missing or throw (private windows, blocked storage): the app then simply does not persist. */
function store(): Storage | null {
  try {
    return window.sessionStorage
  } catch {
    return null
  }
}

export function saveSession(s: SessionState, st: Storage | null = store()): void {
  try {
    st?.setItem(SESSION_KEY, serialize(s))
  } catch {
    /* quota or blocked: ignore */
  }
}

export function loadSession(st: Storage | null = store()): SessionState | null {
  try {
    return restore(st?.getItem(SESSION_KEY) ?? null)
  } catch {
    return null
  }
}

export function clearSession(st: Storage | null = store()): void {
  try {
    st?.removeItem(SESSION_KEY)
  } catch {
    /* ignore */
  }
}
