import { localize } from './locale'

/** Single number formatter for ticks, lane widths, legend and cursor table (RI-27). No units: the KVT has none. */

/** Decimals needed to show `sig` significant digits of v. */
export function sigDecimals(v: number, sig = 3): number {
  const a = Math.abs(v)
  if (!isFinite(a) || a === 0) return 0
  return Math.max(0, sig - 1 - Math.floor(Math.log10(a)))
}

/** Decimals of a tick step: its exact decimals, but never more than 3 significant digits. */
export function stepDecimals(step: number): number {
  const s = Math.abs(step)
  if (!isFinite(s) || s === 0) return 0
  for (let d = 0; d <= 10; d++) {
    if (Math.abs(Math.round(s * 10 ** d) / 10 ** d - s) <= 1e-9 * s) return Math.min(d, sigDecimals(s))
  }
  return sigDecimals(s)
}

export const isExponential = (v: number) => {
  const a = Math.abs(v)
  return a >= 1e6 || (a > 0 && a < 1e-3)
}

export interface NumberFormat {
  decimals: number
  expDigits: number
  format(v: number): string
}

const clean = (s: string) => (/^-0(\.0*)?$/.test(s) ? s.slice(1) : s)

/** decimals = max(3 significant digits of the largest value, decimals of the tick step). */
export function makeFormat(maxAbs: number, step: number, extraDecimals = 0, extraExp = 0): NumberFormat {
  const decimals = Math.max(sigDecimals(maxAbs), stepDecimals(step)) + extraDecimals
  const expDigits = 2 + extraExp
  return {
    decimals,
    expDigits,
    format(v: number) {
      if (!isFinite(v)) return String(v)
      if (isExponential(v)) return localize(clean(v.toExponential(expDigits)))
      return localize(clean(v.toFixed(decimals)))
    },
  }
}

/** Labels for the given tick values, made distinguishable from each other by adding digits if needed. */
export function formatTicks(values: number[], maxAbs: number, step: number): string[] {
  for (let extra = 0; extra < 8; extra++) {
    const f = makeFormat(maxAbs, step, extra, extra)
    const labels = values.map((v) => f.format(v))
    if (new Set(labels).size === labels.length) return labels
  }
  const f = makeFormat(maxAbs, step, 8, 8)
  return values.map((v) => f.format(v))
}

/** Formatter for the values of one channel in legend, cursor table and statistics (3 significant digits of its largest value). */
export function channelFormat(vMin: number | null | undefined, vMax: number | null | undefined): NumberFormat {
  const maxAbs = Math.max(Math.abs(vMin ?? 0), Math.abs(vMax ?? 0))
  return makeFormat(maxAbs, 0)
}
