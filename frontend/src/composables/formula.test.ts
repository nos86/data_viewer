import { describe, expect, it } from 'vitest'
import {
  addCalcBlock, addChBlock, addConstBlock, addOpBlock, blockById, connect, disconnect, levels, newFormula, reachable, removeBlock, syncFormulaUid, validate,
  wouldCalcCycle, wouldCycle, type Formula, type OpMeta,
} from './formula'

const meta = (name: string, inputs: number, temporal = false): OpMeta => ({
  name, version: 1, nature: temporal ? 'temporal' : 'pointwise', inputs: Array.from({ length: inputs }, (_, i) => ({ name: 'abc'[i] })),
  params: temporal ? [{ name: 'window_s', type: 'float', default: 1 }] : [],
})
const map = (...fs: Formula[]) => new Map(fs.map((f) => [f.id, f]))

function sample() {
  const f = newFormula('f')
  const a = addChBlock(f, 'A', 'x', 'linear')
  const b = addChBlock(f, 'A', 'y', 'linear')
  const sub = addOpBlock(f, meta('sub', 2))
  const avg = addOpBlock(f, meta('moving_average', 1, true))
  return { f, a, b, sub, avg }
}

describe('blocks (RF-28)', () => {
  it('has the four block types; the first operator becomes the output', () => {
    const { f, sub } = sample()
    const k = addConstBlock(f, 3)
    const c = addCalcBlock(f, 'Fx')
    expect(f.blocks.map((b) => b.type)).toEqual(['ch', 'ch', 'op', 'op', 'const', 'calc'])
    expect(f.output).toBe(sub.id)
    expect(k.value).toBe(3)
    expect(c.formulaId).toBe('Fx')
  })
  it('new operator blocks start with the catalogue defaults and unconnected inputs', () => {
    const { avg } = sample()
    expect(avg.params).toEqual({ window_s: 1 })
    expect(avg.inputs).toEqual([null])
  })
  it('positions are kept outside the graph', () => {
    const { f, a } = sample()
    expect(f.positions[a.id]).toBeDefined()
    expect('x' in (a as object)).toBe(false)
  })
  it('removing a block disconnects its consumers and clears the output', () => {
    const { f, a, sub } = sample()
    connect(map(f), f, sub.id, 0, a.id)
    removeBlock(f, a.id)
    expect(sub.inputs[0]).toBeNull()
    expect(f.positions[a.id]).toBeUndefined()
    removeBlock(f, sub.id)
    expect(f.output).toBeNull()
  })
})

describe('cycle prevention (RF-30)', () => {
  it('connects valid links and refuses wrong targets', () => {
    const { f, a, sub } = sample()
    expect(connect(map(f), f, sub.id, 0, a.id)).toEqual({ ok: true })
    expect(sub.inputs[0]).toBe(a.id)
    expect(connect(map(f), f, a.id, 0, sub.id).ok).toBe(false) // channels have no inputs
    expect(connect(map(f), f, sub.id, 5, a.id).ok).toBe(false)
  })
  it('refuses a block feeding itself and indirect loops', () => {
    const { f, a, sub, avg } = sample()
    expect(connect(map(f), f, sub.id, 0, sub.id)).toEqual({ ok: false, reason: 'This link would create a loop' })
    connect(map(f), f, sub.id, 0, a.id)
    connect(map(f), f, avg.id, 0, sub.id)
    expect(wouldCycle(map(f), f, sub.id, avg.id)).toBe(true)
    const r = connect(map(f), f, sub.id, 1, avg.id)
    expect(r.ok).toBe(false)
    expect(sub.inputs[1]).toBeNull()
  })
  it('detects loops that pass through calculated-channel blocks of other formulas', () => {
    const f = newFormula('f')
    const g = newFormula('g')
    const fo = addOpBlock(f, meta('neg', 1))
    const go = addOpBlock(g, meta('neg', 1))
    const gc = addCalcBlock(g, f.id) // g uses f
    connect(map(f, g), g, go.id, 0, gc.id)
    expect(wouldCalcCycle(map(f, g), f.id, g.id)).toBe(true) // f using g would close a loop
    expect(wouldCalcCycle(map(f, g), g.id, f.id)).toBe(false)
    expect(wouldCalcCycle(map(f, g), f.id, f.id)).toBe(true)
    // linking inside f from a calc block of g: f -> calc(g) -> f
    const fc = addCalcBlock(f, g.id)
    expect(wouldCycle(map(f, g), f, fo.id, fc.id)).toBe(true)
  })
  it('disconnecting frees the input', () => {
    const { f, a, sub } = sample()
    connect(map(f), f, sub.id, 0, a.id)
    disconnect(f, sub.id, 0)
    expect(sub.inputs[0]).toBeNull()
  })
})

describe('evaluation order (RI-23)', () => {
  it('groups independent operators in the same level', () => {
    const f = newFormula('f')
    const x = addChBlock(f, 'A', 'x', 'linear')
    const y = addChBlock(f, 'A', 'y', 'linear')
    const neg = addOpBlock(f, meta('neg', 1))
    const abs = addOpBlock(f, meta('abs', 1))
    const add = addOpBlock(f, meta('add', 2))
    const avg = addOpBlock(f, meta('moving_average', 1, true))
    connect(map(f), f, neg.id, 0, x.id)
    connect(map(f), f, abs.id, 0, y.id)
    connect(map(f), f, add.id, 0, neg.id)
    connect(map(f), f, add.id, 1, abs.id)
    connect(map(f), f, avg.id, 0, add.id)
    f.output = avg.id
    expect(levels(f).map((l) => l.sort())).toEqual([[neg.id, abs.id].sort(), [add.id], [avg.id]])
  })
  it('ignores blocks the output does not need', () => {
    const { f, a, sub, avg } = sample()
    connect(map(f), f, sub.id, 0, a.id)
    f.output = sub.id
    expect(reachable(f).has(avg.id)).toBe(false)
    expect(levels(f)).toEqual([[sub.id]])
  })
  it('a shared block appears once, before both consumers', () => {
    const f = newFormula('f')
    const x = addChBlock(f, 'A', 'x', 'linear')
    const neg = addOpBlock(f, meta('neg', 1))
    const add = addOpBlock(f, meta('add', 2))
    connect(map(f), f, neg.id, 0, x.id)
    connect(map(f), f, add.id, 0, neg.id)
    connect(map(f), f, add.id, 1, neg.id)
    f.output = add.id
    expect(levels(f)).toEqual([[neg.id], [add.id]])
  })
})

describe('validation', () => {
  it('asks for an output, connected inputs and known operators', () => {
    const f = newFormula('f')
    expect(validate(f)).toEqual([{ blockId: null, message: 'Choose the output block' }])
    const sub = addOpBlock(f, meta('sub', 2))
    const cat = new Map([['sub', meta('sub', 2)]])
    expect(validate(f, cat).map((p) => p.message)).toEqual(['Input a is not connected', 'Input b is not connected'])
    const x = addChBlock(f, 'A', 'x', 'linear')
    connect(map(f), f, sub.id, 0, x.id)
    connect(map(f), f, sub.id, 1, x.id)
    expect(validate(f, cat)).toEqual([])
    expect(validate(f, new Map()).map((p) => p.message)).toEqual(['Unknown operator sub'])
    f.output = x.id
    expect(validate(f, cat)[0].message).toContain('output must be')
    expect(blockById(f, x.id)).toBeDefined()
  })
  it('new ids continue after restored ones', () => {
    const f = newFormula('f')
    f.blocks.push({ id: 'b900', type: 'const', value: 1 })
    f.id = 'F901'
    syncFormulaUid([f])
    expect(Number(newFormula('g').id.slice(1))).toBeGreaterThan(901)
  })
})
