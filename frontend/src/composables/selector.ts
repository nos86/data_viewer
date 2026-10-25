/** The measurement selector (RF-01): VIN, configuration and start-of-sampling range, sent to GET /files as query parameters. */
export interface Selector {
  vin: string
  configuration: string
  from: string // yyyy-mm-dd or ''
  to: string
}

export const emptySelector = (): Selector => ({ vin: '', configuration: '', from: '', to: '' })

export const selectorActive = (s: Selector) => !!(s.vin.trim() || s.configuration.trim() || s.from || s.to)

/** Query string for the backend: local dates become whole-day bounds (the metadata holds local time, RI-05). */
export function selectorQuery(s: Selector): string {
  const q = new URLSearchParams()
  if (s.vin.trim()) q.set('vin', s.vin.trim())
  if (s.configuration.trim()) q.set('configuration_id', s.configuration.trim())
  if (s.from) q.set('start_from', `${s.from}T00:00:00`)
  if (s.to) q.set('start_to', `${s.to}T23:59:59.999999`)
  const text = q.toString()
  return text ? `?${text}` : ''
}
