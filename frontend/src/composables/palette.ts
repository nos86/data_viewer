/** Line colours (RF-40) and the maths used to check that they stay distinguishable for colour-vision deficiencies. */
import type { Dash } from './layout'

// Okabe-Ito based, extended by a greedy search that maximises the smallest colour difference under normal, protan, deutan and tritan
// vision (see palette.test.ts); order matters: the first colours are the ones that stay apart best
export const PALETTE = ['#0072B2', '#E69F00', '#009E73', '#D55E00', '#CC79A7', '#56B4E9', '#000000', '#332288', '#DDCC77', '#8B5A2B', '#882255', '#BBBBBB']

export const DASH_CYCLE: Dash[] = ['solid', 'dashed', 'dotted']

export type Vision = 'normal' | 'protanopia' | 'deuteranopia' | 'tritanopia'
export type RGB = [number, number, number]

// Machado, Oliveira & Fernandes (2009), severity 1.0, applied on linear RGB
const MATRIX: Record<Exclude<Vision, 'normal'>, number[][]> = {
  protanopia: [[0.152286, 1.052583, -0.204868], [0.114503, 0.786281, 0.099216], [-0.003882, -0.048116, 1.051998]],
  deuteranopia: [[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413], [-0.01182, 0.04294, 0.968881]],
  tritanopia: [[1.255528, -0.076749, -0.178779], [-0.078411, 0.930809, 0.147602], [0.004733, 0.691367, 0.3039]],
}

export function hexToRgb(hex: string): RGB {
  const n = parseInt(hex.replace('#', ''), 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

const toLinear = (c: number) => ((c /= 255) <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4)

/** CIE L*a*b* (D65) of an sRGB colour as seen with the given vision type. */
export function lab(hex: string, vision: Vision = 'normal'): [number, number, number] {
  let [r, g, b] = hexToRgb(hex).map(toLinear)
  if (vision !== 'normal') {
    const m = MATRIX[vision]
    ;[r, g, b] = [0, 1, 2].map((i) => Math.min(1, Math.max(0, m[i][0] * r + m[i][1] * g + m[i][2] * b)))
  }
  const x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
  const y = 0.2126 * r + 0.7152 * g + 0.0722 * b
  const z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
  const f = (t: number) => (t > 216 / 24389 ? Math.cbrt(t) : (24389 / 27 * t + 16) / 116)
  return [116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))]
}

export function deltaE(a: string, b: string, vision: Vision = 'normal'): number {
  const p = lab(a, vision)
  const q = lab(b, vision)
  return Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2])
}

/** Smallest colour difference between any two colours of the list, and the pair that has it. */
export function minDistance(colors: string[], vision: Vision): { de: number; pair: [string, string] } {
  let best = { de: Infinity, pair: ['', ''] as [string, string] }
  for (let i = 0; i < colors.length; i++)
    for (let j = i + 1; j < colors.length; j++) {
      const de = deltaE(colors[i], colors[j], vision)
      if (de < best.de) best = { de, pair: [colors[i], colors[j]] }
    }
  return best
}

/** Style of the nth line of a tab: palette colour, and once the palette has been used up the same colours again with another dash. */
export function styleFor(index: number, used: Set<string>): { color: string; dash: Dash } {
  const free = PALETTE.find((c) => !used.has(c))
  if (free) return { color: free, dash: 'solid' }
  const round = Math.floor(index / PALETTE.length) % DASH_CYCLE.length
  return { color: PALETTE[index % PALETTE.length], dash: DASH_CYCLE[round || 1] }
}
