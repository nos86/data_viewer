/** Orchestration of a formula (RI-23): topological levels, parallel calls for independent branches, handles between calls,
 * one automatic retry when the server lost a handle (RI-20). Channel blocks need no call: they are inputs of their consumers. */
import { ApiError, isUnknownHandle, type OperatorOutcome, type OperatorRequest, type SeriesData } from '../api/client'
import { blockById, levels, reachable, validate, type Block, type Formula } from './formula'

export interface BlockResult {
  count: number
  nulls: number
  min: number | null
  max: number | null
  last: number | null
  sparkline: (number | null)[][]
  coincident: number | null
  n: number | null
  error?: string
}

export interface FormulaResult {
  blocks: Record<string, BlockResult> // preview of every evaluated block (RF-29)
  output?: { handle: string; series?: SeriesData; discrete: boolean; stats: { min: number | null; max: number | null }; coincident: number | null }
  error?: string
}

export interface EvalApi {
  applyOperator(name: string, body: OperatorRequest): Promise<OperatorOutcome>
}

export interface EvalContext {
  api: EvalApi
  formulas: Map<string, Formula>
  /** measurement label -> open measurement id; undefined when that slot is not open */
  slot(label: string): string | undefined
  /** shifts in steps per open measurement (RF-03) */
  shifts: Record<string, number>
  /** called before each parallel level, for tests and progress */
  onLevel?: (ids: string[]) => void
}

type Ref = { ch: { file: string; channel: string; mode: string } } | { handle: string } | { const: number }

const empty = (error: string): BlockResult => ({ count: 0, nulls: 0, min: null, max: null, last: null, sparkline: [], coincident: null, n: null, error })

function toResult(o: OperatorOutcome): BlockResult {
  const r = o.result
  const s = r.stats
  return { count: s.count, nulls: s.nulls, min: s.min ?? null, max: s.max ?? null, last: s.last ?? null, sparkline: r.sparkline, coincident: r.coincident, n: r.n }
}

async function run(f: Formula, ctx: EvalContext, full: boolean, visiting: Set<string>): Promise<FormulaResult> {
  const out: FormulaResult = { blocks: {} }
  const probs = validate(f)
  if (probs.length) return { ...out, error: probs[0].message }
  if (visiting.has(f.id)) return { ...out, error: 'The formula uses itself' }
  visiting.add(f.id)
  try {
    const need = reachable(f)
    const refs = new Map<string, Ref>()
    const failed = new Map<string, string>()

    // sources: channels and constants become call inputs, calculated channels are evaluated first (recursively)
    const calcs: Promise<void>[] = []
    for (const id of need) {
      const b: Block | undefined = blockById(f, id)
      if (!b) continue
      if (b.type === 'ch') {
        const file = ctx.slot(b.slot)
        if (!file) failed.set(id, `No measurement is open as ${b.slot}`)
        else refs.set(id, { ch: { file, channel: b.channel, mode: b.mode } })
      } else if (b.type === 'const') refs.set(id, { const: b.value })
      else if (b.type === 'calc') {
        const g = ctx.formulas.get(b.formulaId)
        if (!g) {
          failed.set(id, 'The calculated channel no longer exists')
          continue
        }
        calcs.push(
          run(g, ctx, full && id === f.output, visiting).then((r) => {
            if (r.error || !r.output) failed.set(id, r.error ?? 'The calculated channel failed')
            else {
              refs.set(id, { handle: r.output.handle })
              if (id === f.output) out.output = r.output // a formula whose output is a calculated channel is that channel
            }
          }),
        )
      }
    }
    await Promise.all(calcs)

    for (const ids of levels(f)) {
      ctx.onLevel?.(ids)
      await Promise.all(
        ids.map(async (id) => {
          const b = blockById(f, id)
          if (b?.type !== 'op') return
          const bad = b.inputs.find((i) => i && failed.has(i))
          if (bad) {
            failed.set(id, 'An input of this block has an error')
            return
          }
          const inputs = b.inputs.map((i) => refs.get(i as string))
          if (inputs.some((x) => !x)) return void failed.set(id, 'An input is not connected')
          const isOut = id === f.output
          try {
            const o = await ctx.api.applyOperator(b.op, {
              inputs: inputs as OperatorRequest['inputs'],
              params: b.params,
              shifts: ctx.shifts,
              detail: isOut && full ? 'full' : 'summary',
            })
            refs.set(id, { handle: o.result.handle })
            out.blocks[id] = toResult(o)
            if (isOut) {
              out.output = {
                handle: o.result.handle,
                series: o.series,
                discrete: o.result.discrete,
                stats: { min: o.result.stats.min ?? null, max: o.result.stats.max ?? null },
                coincident: o.result.coincident,
              }
            }
          } catch (e) {
            if (isUnknownHandle(e)) throw e // the whole chain is recomputed by the caller
            failed.set(id, e instanceof Error ? e.message : String(e))
          }
        }),
      )
    }
    for (const [id, msg] of failed) out.blocks[id] = empty(msg)
    if (f.output && failed.has(f.output)) out.error = failed.get(f.output)
    else if (!out.output) out.error = 'The output block could not be computed'
    return out
  } finally {
    visiting.delete(f.id)
  }
}

/** Evaluates `formula`; with `full` the output series is returned too. Retries once if the server forgot a handle. */
export async function evaluateFormula(formula: Formula, ctx: EvalContext, full = true): Promise<FormulaResult> {
  for (let attempt = 0; ; attempt++) {
    try {
      return await run(formula, ctx, full, new Set())
    } catch (e) {
      if (isUnknownHandle(e) && attempt === 0) continue
      if (e instanceof ApiError || e instanceof Error) return { blocks: {}, error: e.message }
      throw e
    }
  }
}
