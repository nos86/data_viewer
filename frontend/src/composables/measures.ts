/** Several open measurements on one time axis (RF-01…05). The first is the primary: its time is the cursor time.
 * Shifts are integer lattice steps (RF-03, RI-25); seconds appear only at the UI edge. */
import { snapK } from './cursors'

export const MAX_OPEN = 4
export const LABELS = ['A', 'B', 'C', 'D']

export interface OpenMeasure {
  id: string
  label: string // short label shown on every series when several are open (RF-17)
  name: string
  shiftK: number // steps; k_primary = k_own + shiftK; always 0 for the primary
  durationUs: number | null // known once the conversion is done
}

export const durationK = (m: OpenMeasure, stepBaseUs: number) => (m.durationUs == null ? 0 : Math.floor(m.durationUs / stepBaseUs))

/** Opens a measurement with the first free label; null if already open or the limit (4) is reached. */
export function openMeasure(list: OpenMeasure[], id: string, name: string, shiftK = 0): OpenMeasure | null {
  if (list.some((m) => m.id === id) || list.length >= MAX_OPEN) return null
  const label = LABELS.find((l) => !list.some((m) => m.label === l)) as string
  const m: OpenMeasure = { id, label, name, shiftK: list.length ? shiftK : 0, durationUs: null }
  list.push(m)
  return m
}

/** Closes a measurement; closing the primary promotes the next one (see `makePrimary` for the re-basing). Returns the re-basing delta. */
export function closeMeasure(list: OpenMeasure[], id: string): number {
  const i = list.findIndex((m) => m.id === id)
  if (i < 0) return 0
  list.splice(i, 1)
  return i === 0 && list.length ? rebase(list, 0) : 0
}

/** Moves a measurement to the front. Returns the shift the new primary had: cursors and zoom, expressed in the old primary time, must move by −delta. */
export function makePrimary(list: OpenMeasure[], id: string): number {
  const i = list.findIndex((m) => m.id === id)
  if (i <= 0) return 0
  const [m] = list.splice(i, 1)
  list.unshift(m)
  return rebase(list, 0)
}

function rebase(list: OpenMeasure[], idx: number): number {
  const delta = list[idx].shiftK
  for (const m of list) m.shiftK -= delta
  return delta
}

/** RF-03: seconds → steps (rounded to 0.1 s); the UI shows `shiftSeconds` back, i.e. the applied value. */
export const setShiftSeconds = (m: OpenMeasure, seconds: number, stepBaseUs: number) => {
  if (Number.isFinite(seconds)) m.shiftK = snapK(seconds, stepBaseUs)
}
export const shiftSeconds = (m: OpenMeasure, stepBaseUs: number) => (m.shiftK * stepBaseUs) / 1e6

/** RF-04: the selected measurement starts at the cursor (its t = 0 lands on the cursor time). */
export function t0OnCursor(list: OpenMeasure[], id: string, cursorK: number): boolean {
  const m = list.find((x) => x.id === id)
  if (!m || m === list[0]) return false
  m.shiftK = cursorK
  return true
}

/** Shared axis (RF-02): union of all measurements, negative times allowed. In steps. */
export function extentK(list: OpenMeasure[], stepBaseUs: number): { min: number; max: number } {
  let min = 0
  let max = 0
  for (const m of list) {
    min = Math.min(min, m.shiftK)
    max = Math.max(max, m.shiftK + durationK(m, stepBaseUs))
  }
  return { min, max }
}

/** Measurement k (own time) at primary-time k. */
export const ownK = (m: Pick<OpenMeasure, 'shiftK'>, k: number) => k - m.shiftK
