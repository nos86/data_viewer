/** XY graphs (RF-18): points at the times the two channels have in common, measured grid values only.
 * Index arithmetic on integer k (RI-17, RI-25, RI-24): no interpolation, no filling, holes drop the point. */

export interface XYSeries {
  n: number
  jStart: number
  values: Float64Array // NaN = hole
  shiftK: number // measurement shift: primary k of grid point j is j·n + shiftK
}

export interface XYPoints {
  k: number[] // primary time of each point (increasing)
  x: number[]
  y: number[]
}

const at = (s: XYSeries, k: number): number | null => {
  const d = k - s.shiftK
  if (d < 0 || d % s.n !== 0) return null
  const i = d / s.n - s.jStart
  if (i < 0 || i >= s.values.length) return null
  const v = s.values[i]
  return Number.isNaN(v) ? null : v
}

/** Walks the coarser grid (n is a chain element, so the finer grid either contains all its points or none) and keeps coincident, non-null pairs. */
export function xyPoints(xs: XYSeries, ys: XYSeries): XYPoints {
  const out: XYPoints = { k: [], x: [], y: [] }
  const coarse = xs.n >= ys.n ? xs : ys
  for (let i = 0; i < coarse.values.length; i++) {
    const k = (i + coarse.jStart) * coarse.n + coarse.shiftK
    const x = at(xs, k)
    const y = at(ys, k)
    if (x == null || y == null) continue
    out.k.push(k)
    out.x.push(x)
    out.y.push(y)
  }
  return out
}

/** Index of the last point at or before k (the highlighted point of a cursor), or -1. */
export function pointIndexAt(p: XYPoints, k: number): number {
  let lo = 0
  let hi = p.k.length - 1
  let r = -1
  while (lo <= hi) {
    const m = (lo + hi) >> 1
    if (p.k[m] <= k) {
      r = m
      lo = m + 1
    } else hi = m - 1
  }
  return r
}

/** Inclusive index range of the points with k1 <= k <= k2 (the path highlighted for a band), or null if none. */
export function segmentRange(p: XYPoints, k1: number, k2: number): [number, number] | null {
  let lo = pointIndexAt(p, k1 - 1) + 1
  const hi = pointIndexAt(p, k2)
  if (lo > hi || hi < 0) return null
  if (lo < 0) lo = 0
  return [lo, hi]
}
