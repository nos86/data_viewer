/** Formula model: blocks of four types and the pure operations the editor needs (RF-28, RF-30, RI-23). */
import type { Mode } from './layout'

export type Params = Record<string, number | boolean>

export interface ChBlock {
  id: string
  type: 'ch'
  slot: string // measurement label (A…D) the channel is read from; resolved to an open measurement at evaluation time
  channel: string
  mode: Mode
}
export interface ConstBlock {
  id: string
  type: 'const'
  value: number
}
export interface OpBlock {
  id: string
  type: 'op'
  op: string
  version: number
  params: Params
  inputs: (string | null)[] // block id per operator input, null = not connected
}
export interface CalcBlock {
  id: string
  type: 'calc'
  formulaId: string // another formula, used as a calculated channel
}
export type Block = ChBlock | ConstBlock | OpBlock | CalcBlock

export interface Formula {
  id: string
  name: string
  blocks: Block[]
  output: string | null // block whose result is the calculated channel
  positions: Record<string, { x: number; y: number }> // graphic positions, kept apart from the graph (RI-32)
}

/** What the editor needs to know about an operator (from GET /operators). */
export interface OpMeta {
  name: string
  version: number
  nature: 'pointwise' | 'temporal'
  deprecated?: boolean
  inputs: { name: string }[]
  params: { name: string; type: string; default: number | boolean }[]
}

let counter = 0
export const uid = (p: string) => `${p}${++counter}`

export function syncFormulaUid(formulas: Formula[]): void {
  for (const f of formulas)
    for (const id of [f.id, ...f.blocks.map((b) => b.id)]) {
      const n = Number(id.replace(/^\D+/, ''))
      if (Number.isFinite(n) && n > counter) counter = n
    }
}

export const newFormula = (name: string): Formula => ({ id: uid('F'), name, blocks: [], output: null, positions: {} })

const place = (f: Formula, id: string, at?: { x: number; y: number }) => {
  f.positions[id] = at ?? { x: 40 + (f.blocks.length % 4) * 30, y: 40 + f.blocks.length * 28 }
}

export function addChBlock(f: Formula, slot: string, channel: string, mode: Mode, at?: { x: number; y: number }): ChBlock {
  const b: ChBlock = { id: uid('b'), type: 'ch', slot, channel, mode }
  f.blocks.push(b)
  place(f, b.id, at)
  return b
}

export function addConstBlock(f: Formula, value = 1, at?: { x: number; y: number }): ConstBlock {
  const b: ConstBlock = { id: uid('b'), type: 'const', value }
  f.blocks.push(b)
  place(f, b.id, at)
  return b
}

export function addOpBlock(f: Formula, meta: OpMeta, at?: { x: number; y: number }): OpBlock {
  const params: Params = {}
  for (const p of meta.params) params[p.name] = p.default
  const b: OpBlock = { id: uid('b'), type: 'op', op: meta.name, version: meta.version, params, inputs: meta.inputs.map(() => null) }
  f.blocks.push(b)
  place(f, b.id, at)
  if (!f.output) f.output = b.id
  return b
}

export function addCalcBlock(f: Formula, formulaId: string, at?: { x: number; y: number }): CalcBlock {
  const b: CalcBlock = { id: uid('b'), type: 'calc', formulaId }
  f.blocks.push(b)
  place(f, b.id, at)
  return b
}

export function removeBlock(f: Formula, id: string): void {
  f.blocks = f.blocks.filter((b) => b.id !== id)
  delete f.positions[id]
  for (const b of f.blocks) if (b.type === 'op') b.inputs = b.inputs.map((i) => (i === id ? null : i))
  if (f.output === id) f.output = null
}

export const blockById = (f: Formula, id: string) => f.blocks.find((b) => b.id === id)

// ---- dependencies and cycles (RF-30) ---------------------------------------------------------------------

/** Blocks a block takes its data from: an operator's inputs, or the output of the formula a `calc` block uses. */
function dependencies(formulas: Map<string, Formula>, f: Formula, block: Block): { f: Formula; id: string }[] {
  if (block.type === 'op') return block.inputs.filter((i): i is string => !!i).map((id) => ({ f, id }))
  if (block.type === 'calc') {
    const g = formulas.get(block.formulaId)
    return g?.output ? [{ f: g, id: g.output }] : []
  }
  return []
}

/** True if `target` depends, directly or through other formulas, on `source`... searched from `from` downwards. */
function reaches(formulas: Map<string, Formula>, from: { f: Formula; id: string }, goal: { f: Formula; id: string }): boolean {
  const seen = new Set<string>()
  const stack = [from]
  while (stack.length) {
    const cur = stack.pop()!
    if (cur.f.id === goal.f.id && cur.id === goal.id) return true
    const key = `${cur.f.id}/${cur.id}`
    if (seen.has(key)) continue
    seen.add(key)
    const b = blockById(cur.f, cur.id)
    if (b) stack.push(...dependencies(formulas, cur.f, b))
  }
  return false
}

/** Would feeding `source` into `target` (both in formula `f`) close a loop? A block feeding itself counts. */
export function wouldCycle(formulas: Map<string, Formula>, f: Formula, targetId: string, sourceId: string): boolean {
  if (targetId === sourceId) return true
  // the link makes target depend on source: a loop appears if source already depends on target
  return reaches(formulas, { f, id: sourceId }, { f, id: targetId })
}

/** Would a `calc` block in formula `host` referencing `formulaId` close a loop between formulas? */
export function wouldCalcCycle(formulas: Map<string, Formula>, hostId: string, formulaId: string): boolean {
  if (hostId === formulaId) return true
  const seen = new Set<string>()
  const stack = [formulaId]
  while (stack.length) {
    const id = stack.pop()!
    if (id === hostId) return true
    if (seen.has(id)) continue
    seen.add(id)
    const g = formulas.get(id)
    if (g) for (const b of g.blocks) if (b.type === 'calc') stack.push(b.formulaId)
  }
  return false
}

export type ConnectResult = { ok: true } | { ok: false; reason: string }

/** Connects `sourceId` to input `index` of operator block `targetId`; refuses links that create a cycle. */
export function connect(formulas: Map<string, Formula>, f: Formula, targetId: string, index: number, sourceId: string): ConnectResult {
  const t = blockById(f, targetId)
  const s = blockById(f, sourceId)
  if (!t || !s) return { ok: false, reason: 'Unknown block' }
  if (t.type !== 'op') return { ok: false, reason: 'Only operator blocks have inputs' }
  if (index < 0 || index >= t.inputs.length) return { ok: false, reason: 'No such input' }
  if (wouldCycle(formulas, f, targetId, sourceId)) return { ok: false, reason: 'This link would create a loop' }
  t.inputs[index] = sourceId
  return { ok: true }
}

export function disconnect(f: Formula, targetId: string, index: number): void {
  const t = blockById(f, targetId)
  if (t?.type === 'op') t.inputs[index] = null
}

// ---- evaluation order (RI-23) -----------------------------------------------------------------------------

/** Blocks needed by the output, inside this formula. */
export function reachable(f: Formula): Set<string> {
  const out = new Set<string>()
  const stack = f.output ? [f.output] : []
  while (stack.length) {
    const id = stack.pop()!
    if (out.has(id)) continue
    out.add(id)
    const b = blockById(f, id)
    if (b?.type === 'op') for (const i of b.inputs) if (i) stack.push(i)
  }
  return out
}

/** Operator blocks grouped in levels: all blocks of one level are independent and can be computed in parallel. */
export function levels(f: Formula): string[][] {
  const need = reachable(f)
  const depth = new Map<string, number>()
  const visit = (id: string): number => {
    const known = depth.get(id)
    if (known !== undefined) return known
    depth.set(id, 0) // guards against a malformed (cyclic) file
    const b = blockById(f, id)
    const d = b?.type === 'op' ? 1 + Math.max(0, ...b.inputs.filter((i): i is string => !!i).map(visit)) : 0
    depth.set(id, d)
    return d
  }
  const out: string[][] = []
  for (const b of f.blocks) {
    if (b.type !== 'op' || !need.has(b.id)) continue // blocks keep their creation order inside a level
    const id = b.id
    const d = visit(id)
    ;(out[d - 1] ??= []).push(id)
  }
  return out.filter(Boolean)
}

export interface Problem {
  blockId: string | null
  message: string
}

export function validate(f: Formula, catalogue?: Map<string, OpMeta>): Problem[] {
  const out: Problem[] = []
  if (!f.output || !blockById(f, f.output)) return [{ blockId: null, message: 'Choose the output block' }]
  const outBlock = blockById(f, f.output)!
  if (outBlock.type === 'ch' || outBlock.type === 'const') out.push({ blockId: f.output, message: 'The output must be an operator or a calculated channel' })
  for (const id of reachable(f)) {
    const b = blockById(f, id)
    if (b?.type !== 'op') continue
    b.inputs.forEach((i, n) => {
      if (!i) out.push({ blockId: id, message: `Input ${catalogue?.get(b.op)?.inputs[n]?.name ?? n + 1} is not connected` })
    })
    if (catalogue && !catalogue.has(b.op)) out.push({ blockId: id, message: `Unknown operator ${b.op}` })
    else if (catalogue?.get(b.op)?.deprecated) out.push({ blockId: id, message: `Operator ${b.op} is deprecated` })
  }
  return out
}
