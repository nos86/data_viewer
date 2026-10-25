import { afterEach, describe, expect, it } from 'vitest'
import { formatTicks, makeFormat } from './format'
import { decimalSeparator, formatInstant, instantOfStep, localeNumber, localize, setLocale } from './locale'

afterEach(() => setLocale('en'))

describe('decimal separator follows the language (RF-38)', () => {
  it('english keeps the point, italian and german use the comma', () => {
    setLocale('en')
    expect(decimalSeparator()).toBe('.')
    expect(localize('1.50')).toBe('1.50')
    setLocale('it')
    expect(decimalSeparator()).toBe(',')
    expect(localize('1.50')).toBe('1,50')
    setLocale('de')
    expect(localize('2.00e+6')).toBe('2,00e+6')
  })
  it('the shared formatter applies it to ticks, legend and tables', () => {
    setLocale('it')
    expect(makeFormat(1.5, 0.25).format(0.5)).toBe('0,50')
    expect(makeFormat(2e6, 5e5).format(2e6)).toBe('2,00e+6')
    expect(formatTicks([0, 0.5, 1], 1, 0.5)).toEqual(['0,00', '0,50', '1,00'])
    expect(makeFormat(1000, 200).format(400)).toBe('400')       // no decimals, nothing to change
    setLocale('en')
    expect(makeFormat(1.5, 0.25).format(0.5)).toBe('0.50')
  })
  it('labels stay distinguishable after localisation', () => {
    setLocale('de')
    const labels = formatTicks([1000000, 1000100, 1000200], 1000200, 100)
    expect(new Set(labels).size).toBe(3)
  })
  it('counts use the local grouping', () => {
    setLocale('it')
    expect(localeNumber(1234567)).toMatch(/^1\.234\.567$/)
    setLocale('en')
    expect(localeNumber(1234567)).toBe('1,234,567')
  })
})

describe('dates and times in the browser time zone (RF-38)', () => {
  // 2026-10-25 00:30:00.7 UTC: 02:30 in Rome (still summer time), 00:30 in UTC
  const us = Date.UTC(2026, 9, 25, 0, 30, 0, 700) * 1000
  it('shows the same instant in the zone asked for, with tenths of a second', () => {
    setLocale('en')
    expect(formatInstant(us, { timeZone: 'UTC' })).toBe('00:30:00.7')
    expect(formatInstant(us, { timeZone: 'Europe/Rome' })).toBe('02:30:00.7')
    expect(formatInstant(us, { timeZone: 'Europe/Rome', date: true })).toMatch(/Oct 25, 2026.*02:30:00\.7/)
  })
  it('uses the local decimal separator and respects the fall-back hour', () => {
    setLocale('it')
    expect(formatInstant(us, { timeZone: 'Europe/Rome' })).toBe('02:30:00,7')
    // one hour later the wall clock in Rome shows 02:30 again (CET)
    expect(formatInstant(us + 3600 * 1_000_000, { timeZone: 'Europe/Rome' })).toBe('02:30:00,7')
  })
  it('steps of 0.1 s map to exact instants', () => {
    expect(instantOfStep(us, 17, 100_000)).toBe(us + 1_700_000)
    setLocale('en')
    expect(formatInstant(instantOfStep(us, 17, 100_000), { timeZone: 'UTC' })).toBe('00:30:02.4')
  })
})
