/** Calculated channels: formulas, their evaluation against the open measurements and the series they produce (RF-28…33). */
import { computed, reactive, ref, watch, type Ref } from 'vue'
import { api, type ChannelOut, type OperatorMeta, type SeriesData } from '../api/client'
import { removeBlock, type Formula } from './formula'
import { evaluateFormula, type FormulaResult } from './formulaEval'
import { CALC_FILE, seriesKey } from './layout'
import type { OpenMeasure } from './measures'

export interface FormulaDeps {
  open: Ref<OpenMeasure[]>
  shifts: Ref<Record<string, number>>
  ready: Ref<string> // changes whenever a measurement becomes ready or closes
  wanted: Ref<string[]> // formula ids the visible tab needs (RI-30)
  setSeries(key: string, s: SeriesData): void
}

export function useFormulas(deps: FormulaDeps) {
  const formulas = ref<Formula[]>([])
  const operators = ref<OperatorMeta[]>([])
  const results = reactive<Record<string, FormulaResult>>({})
  const busy = reactive<Record<string, boolean>>({})
  const info = reactive<Record<string, ChannelOut>>({}) // catalogue entries of calculated channels, key = formula id
  const editingId = ref<string | null>(null)
  const seq: Record<string, number> = {}

  const formulaMap = computed(() => new Map(formulas.value.map((f) => [f.id, f])))
  const opMeta = computed(() => new Map(operators.value.map((o) => [o.name, o])))

  async function loadOperators() {
    try {
      operators.value = await api.operators()
    } catch {
      operators.value = []
    }
  }

  const slotMap = () => Object.fromEntries(deps.open.value.map((m) => [m.label, m.id]))

  async function evaluate(id: string) {
    const f = formulaMap.value.get(id)
    if (!f) return
    const mine = (seq[id] = (seq[id] ?? 0) + 1)
    busy[id] = true
    const slots = slotMap()
    const r = await evaluateFormula(f, { api, formulas: formulaMap.value, slot: (l) => slots[l], shifts: deps.shifts.value }, true)
    if (mine !== seq[id]) return // a newer evaluation is on its way
    busy[id] = false
    results[id] = r
    const out = r.output
    if (out?.series) {
      const mode = out.discrete ? 'step' : 'linear'
      const s = { ...out.series, mode } as SeriesData
      for (const m of ['step', 'linear'] as const) deps.setSeries(seriesKey(CALC_FILE, id, m), { ...s, mode: m })
      info[id] = {
        name: f.name, samples: s.values.length, n: s.n, event: false, v_min: out.stats.min, v_max: out.stats.max,
        suggested_mode: mode, modes_allowed: ['step', 'linear'], t_min_us: 0, t_max_us: 0, period_us: null,
      } as ChannelOut
    } else if (r.error) delete info[id]
  }

  // re-evaluate what the visible tab (or the open editor) needs whenever a formula, a shift or a measurement changes
  let timer: number | undefined
  watch(
    [() => JSON.stringify(formulas.value), deps.shifts, deps.ready, deps.wanted, editingId],
    () => {
      window.clearTimeout(timer)
      timer = window.setTimeout(() => {
        const ids = new Set(deps.wanted.value)
        if (editingId.value) ids.add(editingId.value)
        ids.forEach((id) => void evaluate(id))
      }, 250)
    },
    { deep: true },
  )

  function remove(id: string): void {
    formulas.value = formulas.value.filter((f) => f.id !== id)
    for (const f of formulas.value) for (const b of [...f.blocks]) if (b.type === 'calc' && b.formulaId === id) removeBlock(f, b.id)
    delete results[id]
    delete info[id]
    if (editingId.value === id) editingId.value = null
  }

  return { formulas, formulaMap, operators, opMeta, results, busy, info, editingId, loadOperators, evaluate, remove }
}
