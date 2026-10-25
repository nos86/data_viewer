import { describe, expect, it } from 'vitest'
import { addCalcBlock, addChBlock, addConstBlock, addOpBlock, connect, newFormula, type OpMeta } from './formula'
import { addLine, addXYChart, CALC_FILE, newLayout } from './layout'
import { aliasesFromChoices, exportTemplate, importTemplate, missingKey, TEMPLATE_VERSION, type ImportContext } from './template'

const meta = (name: string, inputs: number): OpMeta => ({ name, version: 1, nature: 'pointwise', inputs: Array.from({ length: inputs }, (_, i) => ({ name: 'abc'[i] })), params: [] })
const CHANNELS: Record<string, string[]> = { fa: ['x', 'y', 'z'], fb: ['x', 'w'] }
const ctx = (slots: Record<string, string> = { A: 'fa', B: 'fb' }, ops = { sub: { version: 1 }, moving_average: { version: 1 } }): ImportContext => ({
  slots, hasChannel: (f, c) => !!CHANNELS[f]?.includes(c), operators: ops,
})
const SLOT_OF = { fa: 'A', fb: 'B' }

/** Layout with an x(A) line, a calculated line, an XY chart, and a formula (x(A) - w(B)) -> moving average. */
function build() {
  const layout = newLayout()
  const tab = layout.tabs[0]
  const f = newFormula('Smooth difference')
  const x = addChBlock(f, 'A', 'x', 'linear')
  const w = addChBlock(f, 'B', 'w', 'step')
  const sub = addOpBlock(f, meta('sub', 2))
  const avg = addOpBlock(f, meta('moving_average', 1))
  avg.params = { window_s: 2 }
  const m = new Map([[f.id, f]])
  connect(m, f, sub.id, 0, x.id)
  connect(m, f, sub.id, 1, w.id)
  connect(m, f, avg.id, 0, sub.id)
  f.output = avg.id
  const c0 = tab.charts[0]
  addLine(tab, c0, 'fa', 'x', 'linear')
  addLine(tab, c0, 'fb', 'w', 'step')
  const calc = addLine(tab, c0, CALC_FILE, f.id, 'linear')
  calc.title = f.name
  const xy = addXYChart(tab)!
  xy.xy!.x = { fileId: 'fa', channel: 'y', mode: 'linear' }
  addLine(tab, xy, 'fb', 'x', 'linear')
  return { layout, formulas: [f], f, x, w }
}

describe('template export (RF-32, RI-32)', () => {
  it('writes versioned, typed refs, operator versions and positions apart from the graph', () => {
    const { layout, formulas, f, x } = build()
    const t = exportTemplate(layout, formulas, SLOT_OF)
    expect(t.schema_version).toBe(TEMPLATE_VERSION)
    expect(t.operators).toEqual({ sub: 1, moving_average: 1 })
    const tb = t.formulas[0].blocks.find((b) => b.id === x.id)!
    expect(tb).toEqual({ id: x.id, type: 'ch', ref: { ch: 'x', slot: 'A' }, mode: 'linear' })
    expect(t.formulas[0].positions[x.id]).toEqual(f.positions[x.id])
    const lines = t.layout.tabs[0].charts[0].lines
    expect(lines.map((l) => l.ref)).toEqual([{ ch: 'x', slot: 'A' }, { ch: 'w', slot: 'B' }, { calc: f.id }])
    expect('fileId' in lines[0]).toBe(false)
    const xy = t.layout.tabs[0].charts[1]
    expect(xy.xy!.x!.ref).toEqual({ ch: 'y', slot: 'A' })
  })
  it('contains no measurements, shifts, cursors or zoom', () => {
    const b = build()
    const json = JSON.stringify(exportTemplate(b.layout, b.formulas, SLOT_OF))
    expect(json).not.toMatch(/shift|cursor|xWindow|fa"|fb"/)
  })
})

describe('template import', () => {
  it('round-trips against the same measurements', () => {
    const { layout, formulas } = build()
    const r = importTemplate(JSON.parse(JSON.stringify(exportTemplate(layout, formulas, SLOT_OF))), ctx())
    expect(r.ok && r.missing).toEqual([])
    if (!r.ok) throw new Error(r.error)
    expect(r.formulas).toEqual(formulas)
    expect(r.layout).toEqual(layout)
    expect(r.warnings).toEqual([])
  })
  it('maps slots to whichever measurements are open under those labels', () => {
    const { layout, formulas } = build()
    const t = exportTemplate(layout, formulas, SLOT_OF)
    const r = importTemplate(t, ctx({ A: 'other1', B: 'other2' }) && { ...ctx({ A: 'm1', B: 'm2' }), hasChannel: () => true })
    if (!r.ok) throw new Error(r.error)
    expect(r.layout.tabs[0].charts[0].lines.map((l) => l.fileId)).toEqual(['m1', 'm2', CALC_FILE])
    expect(r.layout.tabs[0].charts[0].lines[2].title).toBe('Smooth difference')
  })
  it('reports missing channels (absent in the slot, or slot not open) with where they are used', () => {
    const { layout, formulas } = build()
    const t = exportTemplate(layout, formulas, SLOT_OF)
    const r = importTemplate(t, ctx({ A: 'fa' }))
    if (!r.ok) throw new Error(r.error)
    expect(r.missing.map((m) => m.key).sort()).toEqual(['B|w', 'B|x'])
    const w = r.missing.find((m) => m.key === 'B|w')!
    expect(w.usedBy.some((u) => u.includes('Smooth difference'))).toBe(true)
    expect(w.usedBy.some((u) => u.includes('Tab 1'))).toBe(true)
    const r2 = importTemplate(t, { ...ctx(), hasChannel: (f, c) => !(f === 'fa' && c === 'x') && !!CHANNELS[f]?.includes(c) })
    expect(r2.ok && r2.missing.map((m) => m.key)).toEqual(['A|x'])
  })
  it('applies the user choices: another channel, a constant, a calculated channel, or skip (RF-31)', () => {
    const { layout, formulas, f } = build()
    const g = newFormula('other')
    const t = exportTemplate(layout, [...formulas, g], SLOT_OF)
    const base = ctx({ A: 'fa' })
    const r = importTemplate(t, base, {
      [missingKey('B', 'w')]: { kind: 'const', value: 3 },
      [missingKey('B', 'x')]: { kind: 'ch', slot: 'A', channel: 'z' },
    })
    if (!r.ok) throw new Error(r.error)
    expect(r.missing).toEqual([])
    const fm = r.formulas[0]
    expect(fm.blocks.find((b) => b.type === 'const')).toMatchObject({ value: 3 })
    expect(r.layout.tabs[0].charts[1].lines[0]).toMatchObject({ fileId: 'fa', channel: 'z' })       // XY Y line B|x -> A.z
    expect(r.layout.tabs[0].charts[0].lines.map((l) => l.channel)).toEqual(['x', f.id])              // the const replacement drops the line
    const r2 = importTemplate(t, base, { [missingKey('B', 'w')]: { kind: 'calc', formulaId: g.id }, [missingKey('B', 'x')]: { kind: 'skip' } })
    if (!r2.ok) throw new Error(r2.error)
    expect(r2.formulas[0].blocks.some((b) => b.type === 'calc' && b.formulaId === g.id)).toBe(true)
    expect(r2.layout.tabs[0].charts[1].lines).toEqual([])
    const r3 = importTemplate(t, base, { [missingKey('B', 'w')]: { kind: 'skip' }, [missingKey('B', 'x')]: { kind: 'skip' } })
    if (!r3.ok) throw new Error(r3.error)
    const sub = r3.formulas[0].blocks.find((b) => b.type === 'op' && b.op === 'sub')
    expect(sub && sub.type === 'op' && sub.inputs[1]).toBeNull() // a skipped input leaves the block unconnected
  })
  it('applies aliases stored in the file silently, and counts them', () => {
    const { layout, formulas } = build()
    const t = exportTemplate(layout, formulas, SLOT_OF, [{ from: { slot: 'B', ch: 'w' }, to: { kind: 'ch', slot: 'A', channel: 'z' } }, { from: { slot: 'B', ch: 'x' }, to: { kind: 'const', value: 0 } }])
    const r = importTemplate(t, ctx({ A: 'fa' }))
    if (!r.ok) throw new Error(r.error)
    expect(r.missing).toEqual([])
    expect(r.applied).toBeGreaterThanOrEqual(1)
    expect(r.aliases).toHaveLength(2)
    expect(r.formulas[0].blocks.find((b) => b.type === 'ch' && b.channel === 'z')).toBeDefined()
  })
  it('an alias that points to something unavailable is ignored and the channel is reported', () => {
    const { layout, formulas } = build()
    const t = exportTemplate(layout, formulas, SLOT_OF, [{ from: { slot: 'B', ch: 'w' }, to: { kind: 'ch', slot: 'A', channel: 'nope' } }])
    const r = importTemplate(t, ctx({ A: 'fa' }))
    expect(r.ok && r.missing.some((m) => m.key === 'B|w')).toBe(true)
  })
  it('warns about unknown, changed and deprecated operators', () => {
    const { layout, formulas } = build()
    const t = exportTemplate(layout, formulas, SLOT_OF)
    const r = importTemplate(t, ctx(undefined, { sub: { version: 2, deprecated: true } } as never))
    if (!r.ok) throw new Error(r.error)
    expect(r.warnings.join('\n')).toMatch(/moving_average.*does not provide/)
    expect(r.warnings.join('\n')).toMatch(/sub.*version 1.*version 2/)
    expect(r.warnings.join('\n')).toMatch(/deprecated/)
  })
  it('rejects foreign, future and damaged files with a clear message', () => {
    const { layout, formulas } = build()
    const t = exportTemplate(layout, formulas, SLOT_OF)
    expect(importTemplate(null, ctx())).toEqual({ ok: false, error: 'This file is not a template' })
    expect(importTemplate({ hello: 1 }, ctx())).toMatchObject({ ok: false, error: expect.stringMatching(/not a template/) })
    expect(importTemplate({ ...t, schema_version: 9 }, ctx())).toMatchObject({ ok: false, error: expect.stringMatching(/Unsupported template version 9/) })
    expect(importTemplate({ ...t, formulas: 'x' }, ctx())).toMatchObject({ ok: false, error: expect.stringMatching(/damaged/) })
    const bad = JSON.parse(JSON.stringify(t))
    bad.formulas[0].blocks[0].type = 'wat'
    expect(importTemplate(bad, ctx())).toMatchObject({ ok: false, error: expect.stringMatching(/unknown block type/) })
    const loop = JSON.parse(JSON.stringify(t))
    loop.formulas.push({ id: 'Fz', name: 'z', output: 'q', positions: {}, blocks: [{ id: 'q', type: 'calc', ref: { calc: 'Fz' } }] })
    expect(importTemplate(loop, ctx())).toMatchObject({ ok: false, error: expect.stringMatching(/loop/) })
  })
  it('self-sufficient: formulas travel inside the file, calc lines of a missing formula are dropped', () => {
    const { layout, formulas, f } = build()
    const t = exportTemplate(layout, formulas, SLOT_OF)
    t.formulas = []
    const r = importTemplate(t, ctx())
    if (!r.ok) throw new Error(r.error)
    expect(r.layout.tabs[0].charts[0].lines.some((l) => l.channel === f.id)).toBe(false)
  })
  it('calc blocks referencing other formulas survive the round trip', () => {
    const f = newFormula('f')
    const g = newFormula('g')
    const c = addCalcBlock(f, g.id)
    const k = addConstBlock(f, 2)
    const mul = addOpBlock(f, meta('sub', 2))
    const m = new Map([[f.id, f], [g.id, g]])
    connect(m, f, mul.id, 0, c.id)
    connect(m, f, mul.id, 1, k.id)
    const t = exportTemplate(newLayout(), [f, g], SLOT_OF)
    const r = importTemplate(JSON.parse(JSON.stringify(t)), ctx())
    expect(r.ok && r.formulas).toEqual([f, g])
  })
})

describe('aliases to remember (RF-31)', () => {
  it('turns the chosen replacements into aliases and keeps the others', () => {
    const existing = [{ from: { slot: 'A', ch: 'old' }, to: { kind: 'const' as const, value: 1 } }]
    const choices = { 'B|w': { kind: 'ch' as const, slot: 'A', channel: 'z' }, 'B|x': { kind: 'skip' as const }, 'A|old': { kind: 'const' as const, value: 5 } }
    const out = aliasesFromChoices(choices, new Set(['B|w', 'B|x', 'A|old']), existing)
    expect(out).toEqual([{ from: { slot: 'B', ch: 'w' }, to: { kind: 'ch', slot: 'A', channel: 'z' } }, { from: { slot: 'A', ch: 'old' }, to: { kind: 'const', value: 5 } }])
    expect(aliasesFromChoices(choices, new Set(), existing)).toEqual(existing)
  })
})
