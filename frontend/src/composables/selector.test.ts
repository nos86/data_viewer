import { describe, expect, it } from 'vitest'
import { emptySelector, selectorActive, selectorQuery } from './selector'

describe('selector', () => {
  it('sends nothing when empty', () => {
    expect(selectorQuery(emptySelector())).toBe('')
    expect(selectorActive(emptySelector())).toBe(false)
    expect(selectorActive({ ...emptySelector(), vin: '  ' })).toBe(false)
  })

  it('builds the query with trimmed text and whole-day bounds', () => {
    const q = selectorQuery({ vin: ' VIN 1 ', configuration: 'CFG-A', from: '2026-03-01', to: '2026-03-31' })
    const p = new URLSearchParams(q)
    expect(p.get('vin')).toBe('VIN 1')
    expect(p.get('configuration_id')).toBe('CFG-A')
    expect(p.get('start_from')).toBe('2026-03-01T00:00:00')
    expect(p.get('start_to')).toBe('2026-03-31T23:59:59.999999')
    expect(selectorActive({ ...emptySelector(), to: '2026-03-31' })).toBe(true)
  })
})
