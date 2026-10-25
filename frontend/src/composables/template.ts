/** Template file: layout + formulas as JSON, owned by the user, never stored by the backend (RF-32, RI-32).
 * Self-sufficient (formulas are embedded), typed references (`ch` for a channel of a measurement slot, `calc` for a calculated channel),
 * graphic positions apart from the graph. Open measurements, shifts, cursors and zoom are not part of it. */
import type { AxisCfg } from './axes'
import { wouldCalcCycle, type Block, type Formula, type OpBlock } from './formula'
import { CALC_FILE, type Chart, type Layout, type Line, type Mode, type SeriesRef } from './layout'
import { validFormulas, validLayout } from './session'

export const TEMPLATE_VERSION = 1

export type TRef = { ch: string; slot: string } | { calc: string }

export type Replacement =
  | { kind: 'ch'; slot: string; channel: string }
  | { kind: 'const'; value: number }
  | { kind: 'calc'; formulaId: string }
  | { kind: 'skip' }

export interface Alias {
  from: { slot: string; ch: string }
  to: Replacement
}

type TBlock =
  | { id: string; type: 'ch'; ref: { ch: string; slot: string }; mode: Mode }
  | { id: string; type: 'const'; value: number }
  | { id: string; type: 'op'; op: string; version: number; params: Record<string, number | boolean>; inputs: (string | null)[] }
  | { id: string; type: 'calc'; ref: { calc: string } }

interface TFormula {
  id: string
  name: string
  output: string | null
  blocks: TBlock[]
  positions: Record<string, { x: number; y: number }>
}

type TLine = Omit<Line, 'fileId' | 'channel'> & { ref: TRef }
interface TChart extends Omit<Chart, 'lines' | 'xy'> {
  lines: TLine[]
  xy?: { x: { ref: TRef; mode: Mode } | null; xMin: number | null; xMax: number | null }
}

export interface Template {
  schema_version: typeof TEMPLATE_VERSION
  app: 'viewer'
  operators: Record<string, number> // versions of the operators the formulas were built with
  formulas: TFormula[]
  layout: { tabs: { id: string; name: string; charts: TChart[] }[] }
  aliases: Alias[]
}

// ---- export -------------------------------------------------------------------------------------------

export function exportTemplate(layout: Layout, formulas: Formula[], slotOf: Record<string, string>, aliases: Alias[] = []): Template {
  const ref = (fileId: string, channel: string): TRef => (fileId === CALC_FILE ? { calc: channel } : { ch: channel, slot: slotOf[fileId] ?? '?' })
  const operators: Record<string, number> = {}
  const tf: TFormula[] = formulas.map((f) => ({
    id: f.id,
    name: f.name,
    output: f.output,
    positions: JSON.parse(JSON.stringify(f.positions)),
    blocks: f.blocks.map((b): TBlock => {
      if (b.type === 'ch') return { id: b.id, type: 'ch', ref: { ch: b.channel, slot: b.slot }, mode: b.mode }
      if (b.type === 'calc') return { id: b.id, type: 'calc', ref: { calc: b.formulaId } }
      if (b.type === 'op') {
        operators[b.op] = b.version
        return { id: b.id, type: 'op', op: b.op, version: b.version, params: { ...b.params }, inputs: [...b.inputs] }
      }
      return { id: b.id, type: 'const', value: b.value }
    }),
  }))
  return {
    schema_version: TEMPLATE_VERSION,
    app: 'viewer',
    operators,
    formulas: tf,
    layout: {
      tabs: layout.tabs.map((t) => ({
        id: t.id,
        name: t.name,
        charts: t.charts.map((c): TChart => {
          const { lines, xy, ...rest } = JSON.parse(JSON.stringify(c)) as Chart
          return {
            ...rest,
            lines: lines.map(({ fileId, channel, ...l }) => ({ ...l, ref: ref(fileId, channel) })),
            ...(xy ? { xy: { x: xy.x ? { ref: ref(xy.x.fileId, xy.x.channel), mode: xy.x.mode } : null, xMin: xy.xMin, xMax: xy.xMax } } : {}),
          }
        }),
      })),
    },
    aliases,
  }
}

// ---- import -------------------------------------------------------------------------------------------

export interface ImportContext {
  slots: Record<string, string> // label -> open measurement id
  hasChannel(fileId: string, channel: string): boolean
  operators: Record<string, { version: number; deprecated?: boolean }> // backend catalogue
}

export interface MissingRef {
  key: string // `${slot}|${channel}`
  slot: string
  channel: string
  usedBy: string[]
}

export type ImportResult =
  | { ok: false; error: string }
  | { ok: true; layout: Layout; formulas: Formula[]; missing: MissingRef[]; warnings: string[]; aliases: Alias[]; applied: number }

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)

export const missingKey = (slot: string, channel: string) => `${slot}|${channel}`

/**
 * Validates and converts a template for the currently open measurements. Channels that cannot be found are first looked up in the
 * template's own aliases, then in `choices` (the user's decisions); what remains is reported in `missing` (RF-31). Call again with
 * the choices to get the final result.
 */
export function importTemplate(raw: unknown, ctx: ImportContext, choices: Record<string, Replacement> = {}): ImportResult {
  if (!isObj(raw)) return { ok: false, error: 'This file is not a template' }
  if (raw.app !== 'viewer' || typeof raw.schema_version !== 'number') return { ok: false, error: 'This file is not a template of this application' }
  if (raw.schema_version !== TEMPLATE_VERSION) return { ok: false, error: `Unsupported template version ${raw.schema_version} (this application reads version ${TEMPLATE_VERSION})` }
  if (!Array.isArray(raw.formulas) || !isObj(raw.layout) || !Array.isArray(raw.layout.tabs)) return { ok: false, error: 'The template is damaged (formulas or layout are missing)' }

  const aliases: Alias[] = Array.isArray(raw.aliases)
    ? (raw.aliases as Alias[]).filter((a) => isObj(a) && isObj(a.from) && typeof a.from.slot === 'string' && typeof a.from.ch === 'string' && isObj(a.to) && typeof a.to.kind === 'string')
    : []
  const aliasFor = new Map(aliases.map((a) => [missingKey(a.from.slot, a.from.ch), a.to]))

  const missing = new Map<string, MissingRef>()
  const warnings: string[] = []
  let applied = 0
  const formulaIds = new Set((raw.formulas as { id?: unknown }[]).map((f) => String(f?.id)))

  type Resolved = { kind: 'ch'; fileId: string; slot: string; channel: string } | { kind: 'const'; value: number } | { kind: 'calc'; formulaId: string } | { kind: 'skip' } | { kind: 'missing' }

  function resolve(slot: string, channel: string, where: string): Resolved {
    const file = ctx.slots[slot]
    if (file && ctx.hasChannel(file, channel)) return { kind: 'ch', fileId: file, slot, channel }
    const key = missingKey(slot, channel)
    const rep = choices[key] ?? aliasFor.get(key)
    if (rep) {
      if (rep.kind === 'ch') {
        const f2 = ctx.slots[rep.slot]
        if (f2 && ctx.hasChannel(f2, rep.channel)) {
          if (!choices[key]) applied++
          return { kind: 'ch', fileId: f2, slot: rep.slot, channel: rep.channel }
        }
      } else if (rep.kind === 'calc') {
        if (formulaIds.has(rep.formulaId)) return { kind: 'calc', formulaId: rep.formulaId }
      } else if (rep.kind === 'const' && Number.isFinite(rep.value)) return rep
      else if (rep.kind === 'skip') return rep
    }
    const m = missing.get(key) ?? { key, slot, channel, usedBy: [] }
    if (!m.usedBy.includes(where)) m.usedBy.push(where)
    missing.set(key, m)
    return { kind: 'missing' }
  }

  // formulas ----------------------------------------------------------------------------------------------
  const formulas: Formula[] = []
  for (const tf of raw.formulas as TFormula[]) {
    if (!isObj(tf) || typeof tf.id !== 'string' || !Array.isArray(tf.blocks)) return { ok: false, error: 'The template is damaged (a formula is malformed)' }
    const blocks: Block[] = []
    const dropped = new Set<string>()
    for (const b of tf.blocks) {
      if (!isObj(b) || typeof b.id !== 'string') return { ok: false, error: 'The template is damaged (a block is malformed)' }
      if (b.type === 'ch') {
        const r = resolve(b.ref.slot, b.ref.ch, `formula "${tf.name}"`)
        if (r.kind === 'ch') blocks.push({ id: b.id, type: 'ch', slot: r.slot, channel: r.channel, mode: b.mode })
        else if (r.kind === 'const') blocks.push({ id: b.id, type: 'const', value: r.value })
        else if (r.kind === 'calc') blocks.push({ id: b.id, type: 'calc', formulaId: r.formulaId })
        else if (r.kind === 'skip') dropped.add(b.id) // the user chose to leave this channel out
        else blocks.push({ id: b.id, type: 'ch', slot: b.ref.slot, channel: b.ref.ch, mode: b.mode }) // unresolved: reported in `missing`, kept until decided
      } else if (b.type === 'const') blocks.push({ id: b.id, type: 'const', value: b.value })
      else if (b.type === 'calc') blocks.push({ id: b.id, type: 'calc', formulaId: b.ref.calc })
      else if (b.type === 'op') {
        const meta = ctx.operators[b.op]
        if (!meta) warnings.push(`Formula "${tf.name}" uses operator "${b.op}", which this server does not provide`)
        else {
          if (meta.version !== b.version) warnings.push(`Formula "${tf.name}": operator "${b.op}" was version ${b.version}, the server has version ${meta.version}`)
          if (meta.deprecated) warnings.push(`Operator "${b.op}" (formula "${tf.name}") is deprecated`)
        }
        blocks.push({ id: b.id, type: 'op', op: b.op, version: b.version, params: { ...b.params }, inputs: [...b.inputs] } as OpBlock)
      } else return { ok: false, error: `The template is damaged (unknown block type ${String((b as { type?: unknown }).type)})` }
    }
    for (const b of blocks) if (b.type === 'op') b.inputs = b.inputs.map((i) => (i && dropped.has(i) ? null : i))
    formulas.push({
      id: tf.id,
      name: String(tf.name),
      blocks,
      output: tf.output && !dropped.has(tf.output) ? tf.output : null,
      positions: isObj(tf.positions) ? (tf.positions as Formula['positions']) : {},
    })
  }
  if (!validFormulas(formulas)) return { ok: false, error: 'The template is damaged (invalid formulas)' }
  const fmap = new Map(formulas.map((f) => [f.id, f]))
  for (const f of formulas)
    for (const b of f.blocks)
      if (b.type === 'calc' && (!fmap.has(b.formulaId) || wouldCalcCycle(fmap, f.id, b.formulaId))) return { ok: false, error: `Formula "${f.name}" refers to a calculated channel that does not exist or creates a loop` }

  // layout ------------------------------------------------------------------------------------------------
  const refToSeries = (ref: TRef, mode: Mode, where: string): SeriesRef | 'drop' | 'missing' => {
    if ('calc' in ref) return fmap.has(ref.calc) ? { fileId: CALC_FILE, channel: ref.calc, mode } : 'drop'
    const r = resolve(ref.slot, ref.ch, where)
    if (r.kind === 'ch') return { fileId: r.fileId, channel: r.channel, mode }
    if (r.kind === 'calc') return { fileId: CALC_FILE, channel: r.formulaId, mode }
    return r.kind === 'missing' ? 'missing' : 'drop'
  }
  const tabs: Layout['tabs'] = []
  for (const t of (raw.layout as { tabs: { id: string; name: string; charts: TChart[] }[] }).tabs) {
    if (!isObj(t) || !Array.isArray(t.charts)) return { ok: false, error: 'The template is damaged (a tab is malformed)' }
    const charts: Chart[] = []
    t.charts.forEach((c, ci) => {
      const where = `${t.name}, graph ${ci + 1}`
      const lines: Line[] = []
      for (const l of c.lines ?? []) {
        const { ref, ...rest } = l
        const s = refToSeries(ref, rest.mode, where)
        if (s === 'drop') continue
        if (s === 'missing') continue // reported through `missing`; dropped from this preview
        lines.push({ ...rest, fileId: s.fileId, channel: s.channel, ...(s.fileId === CALC_FILE ? { title: fmap.get(s.channel)?.name } : {}) })
      }
      const { lines: _l, xy, ...rest } = c
      const chart: Chart = { ...rest, lines } as Chart
      if (xy) {
        const x = xy.x ? refToSeries(xy.x.ref, xy.x.mode, where) : null
        chart.xy = { x: x && x !== 'drop' && x !== 'missing' ? x : null, xMin: xy.xMin ?? null, xMax: xy.xMax ?? null }
      }
      charts.push(chart)
    })
    tabs.push({ id: t.id, name: String(t.name), charts })
  }
  const layout: Layout = { tabs }
  if (!validLayout(layout)) return { ok: false, error: 'The template is damaged (invalid layout)' }
  return { ok: true, layout, formulas, missing: [...missing.values()], warnings, aliases, applied }
}

/** Aliases to store in the next saved template: the user's replacement choices marked "remember" (RF-31). */
export function aliasesFromChoices(choices: Record<string, Replacement>, remember: Set<string>, existing: Alias[]): Alias[] {
  const out = existing.filter((a) => !remember.has(missingKey(a.from.slot, a.from.ch)))
  for (const key of remember) {
    const to = choices[key]
    if (!to || to.kind === 'skip') continue
    const [slot, ...rest] = key.split('|')
    out.push({ from: { slot, ch: rest.join('|') }, to })
  }
  return out
}
