import { describe, expect, it } from 'vitest'
import { gridSeconds } from './grid'

describe('gridSeconds', () => {
  it('maps grid index to seconds', () => {
    expect(Array.from(gridSeconds(0, 1, 4, 100_000))).toEqual([0, 0.1, 0.2, 0.3])
  })
  it('honours j_start and n', () => {
    expect(Array.from(gridSeconds(3, 10, 3, 100_000))).toEqual([3, 4, 5])
  })
})
