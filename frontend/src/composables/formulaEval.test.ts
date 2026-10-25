import { describe, expect, it } from 'vitest'
import { ApiError, type OperatorOutcome, type OperatorRequest } from '../api/client'
import { addCalcBlock, addChBlock, addConstBlock, addOpBlock, connect, newFormula, type Formula, type OpMeta } from './formula'
import { evaluateFormula, type EvalContext } from './formulaEval'

const meta = (name: string, inputs: number, temporal = false): OpMeta => ({
  name, version: 1, nature: temporal ? 'temporal' : 'pointwise', inputs: Array.from({ length: inputs }, (_, i) => ({ name: 'abc'[i] })), params: [],
})
const map = (...fs: Formula[]) => new Map(fs.map((f) => [f.id, f]))

interface Call { name: string; body: OperatorRequest }

/** Fake server: handle = name(input handles / channels / constants); can fail on demand and forget handles. */
function fakeApi(opts: { fail?: string; forgetOnce?: string; delay?: number } = {}) {
  const calls: Call[] = []
  const known = new Set<string>()
  let inflight = 0
  let maxInflight = 0
  let forgot = false
  const api = {
    async applyOperator(name: string, body: OperatorRequest): Promise<OperatorOutcome> {
      calls.push({ name, body })
      inflight++
      maxInflight = Math.max(maxInflight, inflight)
      await new Promise((r) => setTimeout(r, opts.delay ?? 5))
      inflight--
      if (opts.fail === name) throw new ApiError(422, `${name} failed`)
      const parts = body.inputs.map((i) => {
        if (i.handle) {
          if (opts.forgetOnce && !forgot && name === opts.forgetOnce) {
            forgot = true
            throw new ApiError(404, 'Unknown handle', { code: 'unknown_handle', handle: i.handle })
          }
          if (!known.has(i.handle)) throw new ApiError(404, 'Unknown handle', { code: 'unknown_handle', handle: i.handle })
          return i.handle
        }
        return i.ch ? `${i.ch.file}.${i.ch.channel}.${i.ch.mode}` : `c${i.const}`
      })
      const handle = `${name}(${parts.join(',')})`
      known.add(handle)
      const full = body.detail === 'full'
      return {
        result: {
          handle, operator: name, n: 1, j_start: 0, length: 3, discrete: false, coincident: 3,
          stats: { count: 3, nulls: 0, min: 0, max: 2, mean: 1, last: 2 }, sparkline: [[0, 2]],
        },
        series: full ? { channel: handle, mode: 'linear', n: 1, jStart: 0, values: Float64Array.from([0, 1, 2]) } : undefined,
      }
    },
  }
  return { api, calls, maxInflight: () => maxInflight, known }
}

const ctx = (api: ReturnType<typeof fakeApi>['api'], formulas: Map<string, Formula>, slots: Record<string, string> = { A: 'fileA', B: 'fileB' }): EvalContext => ({
  api, formulas, slot: (l) => slots[l], shifts: { fileB: 5 },
})

function diamond() {
  const f = newFormula('f')
  const x = addChBlock(f, 'A', 'x', 'linear')
  const y = addChBlock(f, 'B', 'y', 'step')
  const neg = addOpBlock(f, meta('neg', 1))
  const abs = addOpBlock(f, meta('abs', 1))
  const add = addOpBlock(f, meta('add', 2))
  const avg = addOpBlock(f, meta('moving_average', 1, true))
  const m = map(f)
  connect(m, f, neg.id, 0, x.id)
  connect(m, f, abs.id, 0, y.id)
  connect(m, f, add.id, 0, neg.id)
  connect(m, f, add.id, 1, abs.id)
  connect(m, f, avg.id, 0, add.id)
  f.output = avg.id
  return { f, x, y, neg, abs, add, avg }
}

describe('formula orchestration (RI-23)', () => {
  it('runs independent branches in parallel and dependent blocks in order, passing handles', async () => {
    const { f, neg, abs, add, avg } = diamond()
    const fake = fakeApi()
    const seen: string[][] = []
    const r = await evaluateFormula(f, { ...ctx(fake.api, map(f)), onLevel: (ids) => seen.push(ids) })
    expect(r.error).toBeUndefined()
    expect(seen).toEqual([[neg.id, abs.id], [add.id], [avg.id]])
    expect(fake.maxInflight()).toBe(2) // neg and abs together, never more
    const byName = Object.fromEntries(fake.calls.map((c) => [c.name, c.body]))
    expect(byName.neg.inputs).toEqual([{ ch: { file: 'fileA', channel: 'x', mode: 'linear' } }])
    expect(byName.add.inputs).toEqual([{ handle: 'neg(fileA.x.linear)' }, { handle: 'abs(fileB.y.step)' }])
    expect(byName.neg.shifts).toEqual({ fileB: 5 })
    expect(Object.keys(r.blocks).sort()).toEqual([neg.id, abs.id, add.id, avg.id].sort()) // every block has a preview (RF-29)
  })

  it('asks for the full series only on the output block', async () => {
    const { f, avg } = diamond()
    const fake = fakeApi()
    const r = await evaluateFormula(f, ctx(fake.api, map(f)))
    expect(fake.calls.filter((c) => c.body.detail === 'full').map((c) => c.name)).toEqual(['moving_average'])
    expect(r.output?.series?.values).toEqual(Float64Array.from([0, 1, 2]))
    expect(r.output?.handle).toBe(r.blocks[avg.id] && 'moving_average(add(neg(fileA.x.linear),abs(fileB.y.step)))')
    const summary = await evaluateFormula(f, ctx(fakeApi().api, map(f)), false)
    expect(summary.output?.series).toBeUndefined()
  })

  it('constants and calculated channels are call inputs; a calc block evaluates its own formula first', async () => {
    const g = newFormula('g')
    const gx = addChBlock(g, 'A', 'x', 'linear')
    const gneg = addOpBlock(g, meta('neg', 1))
    connect(map(g), g, gneg.id, 0, gx.id)
    const f = newFormula('f')
    const c = addCalcBlock(f, g.id)
    const k = addConstBlock(f, 2)
    const mul = addOpBlock(f, meta('mul', 2))
    const formulas = map(f, g)
    connect(formulas, f, mul.id, 0, c.id)
    connect(formulas, f, mul.id, 1, k.id)
    const fake = fakeApi()
    const r = await evaluateFormula(f, ctx(fake.api, formulas))
    expect(r.error).toBeUndefined()
    expect(fake.calls.map((x) => x.name)).toEqual(['neg', 'mul'])
    expect(fake.calls[1].body.inputs).toEqual([{ handle: 'neg(fileA.x.linear)' }, { const: 2 }])
    expect(fake.calls[0].body.detail).toBe('summary')
  })

  it('attributes an error to its block and skips what depends on it', async () => {
    const { f, neg, abs, add, avg } = diamond()
    const fake = fakeApi({ fail: 'abs' })
    const r = await evaluateFormula(f, ctx(fake.api, map(f)))
    expect(r.blocks[abs.id].error).toBe('abs failed')
    expect(r.blocks[add.id].error).toMatch(/input/)
    expect(r.blocks[avg.id].error).toMatch(/input/)
    expect(r.blocks[neg.id].error).toBeUndefined()
    expect(r.output).toBeUndefined()
    expect(r.error).toBeDefined()
    expect(fake.calls.map((c) => c.name)).not.toContain('add')
  })

  it('reports channels whose measurement slot is not open', async () => {
    const { f, abs } = diamond()
    const fake = fakeApi()
    const r = await evaluateFormula(f, ctx(fake.api, map(f), { A: 'fileA' }))
    expect(r.blocks[abs.id].error).toMatch(/input/)
    expect(r.error).toBeDefined()
    expect(fake.calls.length).toBe(1) // only the neg branch could run
  })

  it('recomputes the whole chain once when the server lost a handle (RI-20)', async () => {
    const { f } = diamond()
    const fake = fakeApi({ forgetOnce: 'add' })
    const r = await evaluateFormula(f, ctx(fake.api, map(f)))
    expect(r.error).toBeUndefined()
    expect(fake.calls.filter((c) => c.name === 'neg').length).toBe(2)
    expect(fake.calls.filter((c) => c.name === 'moving_average').length).toBe(1)
  })

  it('gives up with an explicit error if handles keep disappearing', async () => {
    const { f } = diamond()
    const fake = fakeApi()
    fake.known.clear()
    const api = { applyOperator: async (n: string, b: OperatorRequest) => { throw new ApiError(404, 'Unknown handle h', { code: 'unknown_handle', handle: 'h' }) } }
    const r = await evaluateFormula(f, ctx(api as never, map(f)))
    expect(r.error).toMatch(/Unknown handle/)
  })

  it('refuses an incomplete formula without calling the server', async () => {
    const f = newFormula('f')
    addOpBlock(f, meta('neg', 1))
    const fake = fakeApi()
    const r = await evaluateFormula(f, ctx(fake.api, map(f)))
    expect(r.error).toMatch(/not connected/)
    expect(fake.calls).toEqual([])
  })
})
