import { describe, expect, it } from 'vitest'
import { deltaE, lab, minDistance, PALETTE, styleFor, type Vision } from './palette'

const VISIONS: Vision[] = ['normal', 'protanopia', 'deuteranopia', 'tritanopia']

describe('palette (RF-40)', () => {
  it('colour maths sanity: white, black and a known difference', () => {
    expect(lab('#ffffff')[0]).toBeCloseTo(100, 0)
    expect(lab('#000000')[0]).toBeCloseTo(0, 0)
    expect(deltaE('#ff0000', '#ff0000')).toBe(0)
    expect(deltaE('#000000', '#ffffff')).toBeCloseTo(100, 0)
    // a red/green pair collapses for a deuteranope but not for normal vision
    expect(deltaE('#ff0000', '#00aa00', 'normal')).toBeGreaterThan(2 * deltaE('#ff0000', '#00aa00', 'deuteranopia'))
  })
  it('has no duplicates and enough colours', () => {
    expect(new Set(PALETTE).size).toBe(PALETTE.length)
    expect(PALETTE.length).toBeGreaterThanOrEqual(10)
  })
  for (const v of VISIONS) {
    it(`any two colours are clearly different for ${v} vision`, () => {
      const { de, pair } = minDistance(PALETTE, v)
      expect(de, `${pair.join(' vs ')} under ${v}`).toBeGreaterThanOrEqual(v === 'normal' ? 25 : 14)
    })
  }
  it('the first colours (the ones most graphs use) stay far apart for every vision type', () => {
    for (const v of VISIONS) expect(minDistance(PALETTE.slice(0, 6), v).de).toBeGreaterThanOrEqual(v === 'normal' ? 25 : 16)
  })
  it('picks the first unused colour; when exhausted it reuses colours with a different dash', () => {
    const used = new Set<string>()
    const first = styleFor(0, used)
    expect(first).toEqual({ color: PALETTE[0], dash: 'solid' })
    PALETTE.forEach((c) => used.add(c))
    const again = styleFor(PALETTE.length, used)
    expect(again.dash).not.toBe('solid')
    expect(PALETTE).toContain(again.color)
    expect(styleFor(2 * PALETTE.length, used).dash).not.toBe(again.dash)
  })
})
