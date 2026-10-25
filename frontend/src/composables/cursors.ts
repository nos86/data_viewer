/** Global cursors and bands (RF-21…25). Positions are integer lattice indices k, t = k·Δ (RI-25); seconds only for drawing. */

export interface Cursor {
  id: string
  number: number // visible on the time axis, never reused (RF-24)
  kind: 'single' | 'band'
  k: number // single: position; band: start
  k2: number // band: end (>= k); equal to k for a single cursor
  color?: string // optional (RF-24)
}

export interface CursorState {
  items: Cursor[]
  activeId: string | null
  nextNumber: number
}

export const newCursorState = (): CursorState => ({ items: [], activeId: null, nextNumber: 1 })

/** Allowed positions in steps; negative times exist when a measurement is shifted before the primary (RF-03). A bare number means [0, max]. */
export type Bounds = number | { min: number; max: number }
const lim = (b: Bounds) => (typeof b === 'number' ? { min: 0, max: b } : b)

export const clampK = (k: number, b: Bounds) => {
  const { min, max } = lim(b)
  return Math.max(min, Math.min(Math.round(k), Math.max(min, max)))
}
export const snapK = (seconds: number, stepBaseUs: number) => Math.round((seconds * 1e6) / stepBaseUs)
export const kToSeconds = (k: number, stepBaseUs: number) => (k * stepBaseUs) / 1e6

export function addCursor(st: CursorState, kind: 'single' | 'band', k: number, kb: Bounds, bandWidth = 0): Cursor {
  const kMax = lim(kb).max
  const k1 = clampK(kind === 'band' ? Math.min(k, kMax - bandWidth) : k, kb)
  const c: Cursor = {
    id: `cur${st.nextNumber}`,
    number: st.nextNumber,
    kind,
    k: k1,
    k2: kind === 'band' ? clampK(k1 + Math.max(1, bandWidth), kb) : k1,
  }
  st.nextNumber++
  st.items.push(c)
  st.activeId = c.id
  return c
}

export function removeCursor(st: CursorState, id: string): void {
  const i = st.items.findIndex((c) => c.id === id)
  if (i < 0) return
  st.items.splice(i, 1)
  if (st.activeId === id) st.activeId = st.items[Math.min(i, st.items.length - 1)]?.id ?? null
}

export const activeCursor = (st: CursorState) => st.items.find((c) => c.id === st.activeId) ?? null

export function cycleActive(st: CursorState, dir: 1 | -1): void {
  if (!st.items.length) return
  const i = st.items.findIndex((c) => c.id === st.activeId)
  st.activeId = st.items[(i + dir + st.items.length) % st.items.length].id
}

/** Moves a cursor by dk steps. For a band: 'whole' shifts it keeping its width, 'end'/'start' resize it. */
export function moveCursor(c: Cursor, dk: number, kb: Bounds, edge: 'whole' | 'start' | 'end' = 'whole'): void {
  if (c.kind === 'single') {
    c.k = c.k2 = clampK(c.k + dk, kb)
    return
  }
  const width = c.k2 - c.k
  if (edge === 'whole') {
    const { min, max } = lim(kb)
    const k = Math.max(min, Math.min(c.k + dk, max - width))
    c.k = k
    c.k2 = k + width
  } else {
    setEdge(c, edge, (edge === 'start' ? c.k : c.k2) + dk, kb)
  }
}

/** Sets one edge of a band; dragging an edge past the other swaps them. */
export function setEdge(c: Cursor, edge: 'start' | 'end', k: number, kb: Bounds): void {
  const v = clampK(k, kb)
  const other = edge === 'start' ? c.k2 : c.k
  c.k = Math.min(v, other)
  c.k2 = Math.max(v, other)
}

export function setSingle(c: Cursor, k: number, kb: Bounds): void {
  c.k = c.k2 = clampK(k, kb)
}

/** RF-25: which side of the visible window (seconds) a cursor lies on, null if any part of it is visible. */
export function outOfView(c: Cursor, win: [number, number], stepBaseUs: number): 'left' | 'right' | null {
  const a = kToSeconds(c.k, stepBaseUs)
  const b = kToSeconds(c.k2, stepBaseUs)
  if (b < win[0]) return 'left'
  if (a > win[1]) return 'right'
  return null
}

/** New window with the same span centred on t (seconds), kept inside [0, duration]. */
export function centreWindow(win: [number, number], t: number, duration: number): [number, number] {
  const span = Math.min(win[1] - win[0], duration)
  const min = Math.max(0, Math.min(t - span / 2, duration - span))
  return [min, min + span]
}

export const cursorCentreK = (c: Cursor) => Math.round((c.k + c.k2) / 2)

const COLORS = ['#c0392b', '#2e86c1', '#27ae60', '#8e44ad', '#d68910', '#16a085']
export const cursorColor = (c: Cursor) => c.color ?? COLORS[(c.number - 1) % COLORS.length]
