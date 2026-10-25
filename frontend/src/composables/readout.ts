/** Cursor readings and band statistics (RF-26, RF-27). Presentation only: indexes into the grids the server computed (RI-24). */

export interface GridSeries {
  n: number // grid step in lattice units
  jStart: number
  values: Float64Array // NaN = hole
}

/** Where a measurement sits on the shared axis; absent for an unshifted single measurement. */
export interface Placement {
  shiftK: number
  durK: number // length of the measurement in steps
}

export interface Reading {
  value: number | null
  onGrid: boolean // false when the cursor lies between grid points of this channel (value held from the previous point)
  out?: boolean // the measurement is not running at this time (RF-05): shown as "out of measure", never as the last value
}

/** Value at lattice index k: last grid point at or before it (j = floor(k/n)). Null in holes and outside the series. */
export function valueAt(s: GridSeries, kPrimary: number, p?: Placement): Reading {
  const k = kPrimary - (p?.shiftK ?? 0)
  if (p && (k < 0 || k > p.durK)) return { value: null, onGrid: false, out: true }
  const i = Math.floor(k / s.n) - s.jStart
  const onGrid = k % s.n === 0
  if (i < 0 || i >= s.values.length) return { value: null, onGrid }
  const v = s.values[i]
  return { value: Number.isNaN(v) ? null : v, onGrid }
}

export interface BandStats {
  min: number | null
  max: number | null
  mean: number | null
  start: number | null
  end: number | null
  delta: number | null
  durationS: number
  points: number // grid points inside the band
  holes: number // of which missing (acquisition gaps, RF-27)
}

export function bandStats(s: GridSeries, k1p: number, k2p: number, stepBaseUs: number, p?: Placement): BandStats {
  const sh = p?.shiftK ?? 0
  const k1 = k1p - sh
  const k2 = k2p - sh
  const jFirst = Math.max(Math.ceil(k1 / s.n), p ? 0 : -Infinity)
  const jLast = Math.min(Math.floor(k2 / s.n), p ? Math.floor(p.durK / s.n) : Infinity) // time outside the measurement is not a gap
  let min = Infinity
  let max = -Infinity
  let sum = 0
  let count = 0
  let holes = 0
  const points = Math.max(0, jLast - jFirst + 1)
  for (let j = jFirst; j <= jLast; j++) {
    const i = j - s.jStart
    const v = i >= 0 && i < s.values.length ? s.values[i] : NaN
    if (Number.isNaN(v)) {
      holes++
      continue
    }
    if (v < min) min = v
    if (v > max) max = v
    sum += v
    count++
  }
  const start = valueAt(s, k1p, p).value
  const end = valueAt(s, k2p, p).value
  return {
    min: count ? min : null,
    max: count ? max : null,
    mean: count ? sum / count : null,
    start,
    end,
    delta: start != null && end != null ? end - start : null,
    durationS: ((k2p - k1p) * stepBaseUs) / 1e6,
    points,
    holes,
  }
}
