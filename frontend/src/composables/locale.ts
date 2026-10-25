/** Local formats (RF-38): the decimal separator follows the browser language, dates and times the browser time zone.
 * The interface text stays English. Nothing here changes data: only what is displayed or exported for people. */

let decimal: string | null = null
let lang: string | undefined

const browserLang = () => (typeof navigator !== 'undefined' ? navigator.language : undefined)

/** For tests and for a future language setting; without an argument the browser language is used again. */
export function setLocale(language?: string): void {
  lang = language
  decimal = new Intl.NumberFormat(language ?? browserLang()).formatToParts(1.1).find((p) => p.type === 'decimal')?.value ?? '.'
}

export function decimalSeparator(): string {
  if (decimal === null) setLocale(lang)
  return decimal as string
}

/** Turns a number written with '.' (toFixed / toExponential output) into the local notation. */
export function localize(s: string): string {
  const d = decimalSeparator()
  return d === '.' ? s : s.replace('.', d)
}

export const localeNumber = (n: number, digits?: number) => n.toLocaleString(lang ?? browserLang(), digits === undefined ? undefined : { maximumFractionDigits: digits })

export interface TimeOptions {
  timeZone?: string // default: the browser's
  date?: boolean
}

/** "12:34:56.7", or with the date, in the browser's time zone. `us` = microseconds since the epoch (UTC). */
export function formatInstant(us: number, o: TimeOptions = {}): string {
  const ms = Math.floor(us / 1000)
  const tenth = Math.floor(((us % 1_000_000) + 1_000_000) % 1_000_000 / 100_000)
  const f = new Intl.DateTimeFormat(lang ?? browserLang(), {
    timeZone: o.timeZone,
    ...(o.date ? { year: 'numeric', month: 'short', day: 'numeric' } : {}),
    hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
  })
  const parts = f.formatToParts(new Date(ms))
  // put the tenth of a second right after the seconds, whatever order the locale uses
  return parts.map((p) => (p.type === 'second' ? p.value + decimalSeparator() + tenth : p.value)).join('')
}

/** Absolute time of lattice step k of a measurement that starts at t0 (µs, UTC). Integer arithmetic until the last step. */
export const instantOfStep = (t0Us: number, k: number, stepBaseUs: number) => t0Us + k * stepBaseUs
