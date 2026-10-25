<script setup lang="ts">
/** Block editor (RF-28, RF-29, RF-30). Pure model operations live in composables/formula.ts; this component only renders and forwards. */
import { computed, ref } from 'vue'
import { Handle, Position, VueFlow, type Connection, type EdgeMouseEvent, type NodeDragEvent } from '@vue-flow/core'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import type { ChannelOut, OperatorMeta } from '../api/client'
import {
  addCalcBlock, addChBlock, addConstBlock, addOpBlock, connect, disconnect, removeBlock, validate, wouldCalcCycle, wouldCycle, type Block, type Formula, type OpMeta,
} from '../composables/formula'
import type { FormulaResult } from '../composables/formulaEval'
import { localeNumber, localize } from '../composables/locale'

const props = defineProps<{
  formula: Formula
  formulas: Map<string, Formula>
  operators: OperatorMeta[]
  measures: { slot: string; name: string; channels: ChannelOut[] }[]
  result: FormulaResult | undefined
  busy: boolean
}>()
const emit = defineEmits<{ close: []; useInGraph: [] }>()

const notice = ref('')
let noticeTimer: number | undefined
const say = (m: string) => {
  notice.value = m
  window.clearTimeout(noticeTimer)
  noticeTimer = window.setTimeout(() => (notice.value = ''), 4000)
}

const opByName = computed(() => new Map(props.operators.map((o) => [o.name, o])))
const nextAt = () => ({ x: 60 + (props.formula.blocks.length % 5) * 40, y: 40 + (props.formula.blocks.length % 7) * 50 })

const nodes = computed(() =>
  props.formula.blocks.map((b) => ({ id: b.id, type: 'block', position: props.formula.positions[b.id] ?? { x: 0, y: 0 }, data: { id: b.id } })),
)
const edges = computed(() =>
  props.formula.blocks.flatMap((b) =>
    b.type === 'op' ? b.inputs.flatMap((src, i) => (src ? [{ id: `${b.id}:${i}`, source: src, target: b.id, sourceHandle: 'out', targetHandle: `in${i}`, animated: false }] : [])) : [],
  ),
)

const block = (id: string) => props.formula.blocks.find((b) => b.id === id) as Block
const res = (id: string) => props.result?.blocks[id]
const problems = computed(() => validate(props.formula, opByName.value as unknown as Map<string, OpMeta>))
const problemOf = (id: string) => problems.value.find((p) => p.blockId === id)?.message

/** Checked while dragging a link: a link that would close a loop is never created (RF-30). */
function isValid(c: Connection): boolean {
  const t = block(c.target)
  if (!t || t.type !== 'op' || c.source === c.target) return false
  if (wouldCycle(props.formulas, props.formula, c.target, c.source)) {
    say('This link would create a loop')
    return false
  }
  return true
}

function onConnect(c: Connection) {
  const idx = Number((c.targetHandle ?? '').replace('in', ''))
  const r = connect(props.formulas, props.formula, c.target, idx, c.source)
  if (!r.ok) say(r.reason) // RF-30: a link that would close a loop is refused
}
const onEdgeDouble = (e: EdgeMouseEvent) => {
  const [target, i] = e.edge.id.split(':')
  disconnect(props.formula, target, Number(i))
}
const onDragStop = (e: NodeDragEvent) => {
  props.formula.positions[e.node.id] = { x: e.node.position.x, y: e.node.position.y }
}

// palette
const slotSel = ref(props.measures[0]?.slot ?? '')
const query = ref('')
const chans = computed(() => {
  const m = props.measures.find((x) => x.slot === slotSel.value) ?? props.measures[0]
  const q = query.value.trim().toLowerCase()
  return (m?.channels ?? []).filter((c) => !q || c.name.toLowerCase().includes(q)).slice(0, 60)
})
const addChannel = (c: ChannelOut) => addChBlock(props.formula, slotSel.value || props.measures[0]?.slot || 'A', c.name, c.suggested_mode === 'step' ? 'step' : 'linear', nextAt())
const ops = (nature: string) => props.operators.filter((o) => o.nature === nature && !o.deprecated)
const addOp = (o: OperatorMeta) => addOpBlock(props.formula, o as unknown as OpMeta, nextAt())
const others = computed(() => [...props.formulas.values()].filter((g) => g.id !== props.formula.id && !wouldCalcCycle(props.formulas, props.formula.id, g.id)))

const title = (b: Block) =>
  b.type === 'ch' ? `${b.slot}: ${b.channel}` : b.type === 'const' ? 'Constant' : b.type === 'calc' ? `ƒ ${props.formulas.get(b.formulaId)?.name ?? '(deleted)'}` : b.op
const inputNames = (b: Block) => (b.type === 'op' ? (opByName.value.get(b.op)?.inputs.map((i) => i.name) ?? b.inputs.map((_, i) => String(i + 1))) : [])
const paramSpecs = (b: Block) => (b.type === 'op' ? (opByName.value.get(b.op)?.params ?? []) : [])
const setParam = (b: Block, name: string, v: string, type: string) => {
  if (b.type !== 'op') return
  b.params[name] = type === 'bool' ? v === 'true' : Number(v)
}

/** Min/max band of the sparkline as an SVG path (server-made, 200 bins). */
function spark(id: string): string {
  const r = res(id)
  if (!r || !r.sparkline.length || r.min == null || r.max == null) return ''
  const w = 170
  const h = 30
  const span = r.max - r.min || 1
  const x = (i: number) => (i / Math.max(1, r.sparkline.length - 1)) * w
  const y = (v: number) => h - 2 - ((v - r.min!) / span) * (h - 4)
  const top: string[] = []
  const bottom: string[] = []
  r.sparkline.forEach((p, i) => {
    if (p[0] == null || p[1] == null) return
    top.push(`${x(i).toFixed(1)},${y(p[1]).toFixed(1)}`)
    bottom.unshift(`${x(i).toFixed(1)},${y(p[0]).toFixed(1)}`)
  })
  return top.length ? `M${top.join('L')}L${bottom.join('L')}Z` : ''
}
const fmt = (v: number | null | undefined) => (v == null ? '—' : localize(Math.abs(v) >= 1e5 || (v !== 0 && Math.abs(v) < 1e-3) ? v.toExponential(2) : String(+v.toPrecision(4))))
</script>

<template>
  <div class="overlay" @keydown.stop @keyup.stop>
    <div class="dlg">
      <header class="bar">
        <strong>Calculated channel</strong>
        <input v-model="formula.name" class="name" type="text" placeholder="Name" />
        <span v-if="busy" class="muted">Evaluating…</span>
        <span v-else-if="result?.error" class="err" :title="result.error">⚠ {{ result.error }}</span>
        <span v-else-if="result?.output" class="ok">✓ {{ result.output.stats.min != null ? `${fmt(result.output.stats.min)} … ${fmt(result.output.stats.max)}` : 'no values' }}</span>
        <span v-if="notice" class="notice">{{ notice }}</span>
        <span class="grow"></span>
        <button :disabled="!result?.output" title="Add this channel to the first graph of the current tab" @click="emit('useInGraph')">Use in graph</button>
        <button class="primary" @click="emit('close')">Close</button>
      </header>
      <div class="body">
        <aside class="palette">
          <h4>Channel</h4>
          <div v-if="!measures.length" class="muted">Open a measurement first.</div>
          <template v-else>
            <select v-model="slotSel">
              <option v-for="m in measures" :key="m.slot" :value="m.slot">{{ m.slot }} · {{ m.name }}</option>
            </select>
            <input v-model="query" type="search" placeholder="Search…" />
            <ul class="pick">
              <li v-for="c in chans" :key="c.name"><button :title="'Add ' + c.name" @click="addChannel(c)">{{ c.name }}</button></li>
            </ul>
          </template>
          <h4>Constant</h4>
          <button @click="addConstBlock(formula, 1, nextAt())">+ Constant</button>
          <h4>Operators</h4>
          <div v-for="nature in ['pointwise', 'temporal']" :key="nature">
            <div class="grp">{{ nature === 'pointwise' ? 'Point by point' : 'Over time' }}</div>
            <div class="chips">
              <button v-for="o in ops(nature)" :key="o.name" :title="o.description" @click="addOp(o)">{{ o.name }}</button>
            </div>
          </div>
          <h4>Calculated channel</h4>
          <div v-if="!others.length" class="muted">No other formula can be used here.</div>
          <div class="chips"><button v-for="g in others" :key="g.id" @click="addCalcBlock(formula, g.id, nextAt())">ƒ {{ g.name }}</button></div>
          <p class="muted help">Drag from the dot on the right of a block to an input dot. Double-click a link to remove it. Loops are refused.</p>
        </aside>
        <div class="canvas">
          <VueFlow
            :nodes="nodes"
            :edges="edges"
            :nodes-connectable="true"
            :delete-key-code="null"
            :is-valid-connection="isValid"
            :min-zoom="0.3"
            :max-zoom="1.5"
            fit-view-on-init
            @connect="onConnect"
            @edge-double-click="onEdgeDouble"
            @node-drag-stop="onDragStop"
          >
            <template #node-block="{ id }">
              <div v-if="block(id)" class="blk" :class="[block(id).type, { out: formula.output === id, err: res(id)?.error || problemOf(id) }]">
                <header>
                  <span class="t" :title="title(block(id))">{{ title(block(id)) }}</span>
                  <button class="nodrag" :class="{ on: formula.output === id }" title="Use this block as the result" @click="formula.output = id">out</button>
                  <button class="nodrag" title="Delete block" @click="removeBlock(formula, id)">×</button>
                </header>
                <template v-if="block(id).type === 'op'">
                  <div v-for="(n, i) in inputNames(block(id))" :key="i" class="row">
                    <Handle :id="`in${i}`" type="target" :position="Position.Left" :style="{ top: 36 + i * 22 + 'px' }" />
                    <span>{{ n }}</span>
                  </div>
                  <label v-for="p in paramSpecs(block(id))" :key="p.name" class="row par">
                    {{ p.name }}
                    <select v-if="p.type === 'bool'" class="nodrag" :value="String((block(id) as any).params[p.name])" @change="setParam(block(id), p.name, ($event.target as HTMLSelectElement).value, p.type)">
                      <option value="true">yes</option><option value="false">no</option>
                    </select>
                    <input v-else class="nodrag" type="number" step="any" :min="p.min ?? undefined" :max="p.max ?? undefined" :value="(block(id) as any).params[p.name]" @change="setParam(block(id), p.name, ($event.target as HTMLInputElement).value, p.type)" />
                  </label>
                </template>
                <label v-else-if="block(id).type === 'const'" class="row par">
                  value <input class="nodrag" type="number" step="any" :value="(block(id) as any).value" @change="(block(id) as any).value = Number(($event.target as HTMLInputElement).value)" />
                </label>
                <label v-else-if="block(id).type === 'ch'" class="row par">
                  mode
                  <select v-model="(block(id) as any).mode" class="nodrag"><option>step</option><option>linear</option><option>average</option></select>
                </label>
                <div class="prev">
                  <div v-if="res(id)?.error || problemOf(id)" class="e">{{ res(id)?.error ?? problemOf(id) }}</div>
                  <template v-else-if="res(id)">
                    <svg width="170" height="30" aria-hidden="true"><path :d="spark(id)" fill="#9ec9e6" stroke="#0072b2" stroke-width="0.6" /></svg>
                    <div class="s">{{ localeNumber(res(id)!.count) }} pts · {{ fmt(res(id)!.min) }} … {{ fmt(res(id)!.max) }}</div>
                    <div v-if="res(id)!.nulls" class="s">{{ localeNumber(res(id)!.nulls) }} null</div>
                    <div v-if="res(id)!.coincident != null && res(id)!.coincident! < 2" class="s w">⚠ no common points: check the shifts</div>
                  </template>
                  <div v-else-if="block(id).type === 'ch' || block(id).type === 'const'" class="s muted">input</div>
                  <div v-else class="s muted">no result yet</div>
                </div>
                <Handle id="out" type="source" :position="Position.Right" />
              </div>
            </template>
          </VueFlow>
          <div v-if="!formula.blocks.length" class="hint">Add blocks from the left: a channel and an operator are enough.</div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.overlay { position: fixed; inset: 0; background: rgba(16, 24, 40, 0.45); z-index: 50; display: grid; place-items: center; padding: 24px; }
.dlg { background: var(--bg); border-radius: 10px; width: min(1400px, 100%); height: 100%; display: flex; flex-direction: column; overflow: hidden; border: 1px solid var(--border); }
.bar { display: flex; gap: 10px; align-items: center; padding: 8px 12px; border-bottom: 1px solid var(--border); background: var(--panel); }
.bar .name { font: inherit; padding: 4px 8px; border: 1px solid var(--border); border-radius: 6px; width: 260px; }
.bar .grow { flex: 1; }
.primary { background: var(--accent); color: #fff; border: none; border-radius: 6px; padding: 5px 12px; }
.ok { color: var(--ok); font-size: 12px; }
.err { color: var(--danger); font-size: 12px; max-width: 420px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.notice { color: #93370d; background: #fef0c7; padding: 2px 8px; border-radius: 4px; font-size: 12px; }
.muted { color: var(--muted); font-size: 12px; }
.body { flex: 1; display: flex; min-height: 0; }
.palette { width: 230px; padding: 10px; border-right: 1px solid var(--border); overflow: auto; display: flex; flex-direction: column; gap: 6px; background: var(--panel); }
.palette h4 { margin: 8px 0 0; font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); }
.palette select, .palette input[type='search'] { font: inherit; padding: 4px 6px; border: 1px solid var(--border); border-radius: 4px; }
.pick { list-style: none; margin: 0; padding: 0; max-height: 140px; overflow: auto; border: 1px solid var(--border); border-radius: 4px; background: var(--bg); }
.pick button { width: 100%; text-align: left; border: none; background: none; padding: 2px 6px; font-size: 12px; }
.pick button:hover { background: var(--panel); }
.grp { font-size: 11px; color: var(--muted); margin: 4px 0 2px; }
.chips { display: flex; flex-wrap: wrap; gap: 4px; }
.chips button, .palette > button { font-size: 12px; padding: 2px 8px; border: 1px solid var(--border); background: var(--bg); border-radius: 12px; }
.help { margin-top: 10px; line-height: 1.4; }
.canvas { flex: 1; position: relative; min-width: 0; }
.hint { position: absolute; inset: 0; display: grid; place-items: center; color: var(--muted); pointer-events: none; }
.blk { width: 200px; background: #fff; border: 1px solid #98a2b3; border-radius: 6px; font-size: 12px; position: relative; padding-bottom: 6px; }
.blk.out { border-color: var(--accent); box-shadow: 0 0 0 2px #cfe6f5; }
.blk.err { border-color: var(--danger); }
.blk header { display: flex; align-items: center; gap: 4px; padding: 4px 6px; border-bottom: 1px solid var(--border); border-radius: 6px 6px 0 0; height: 26px; }
.blk.ch header { background: #e6f2fa; } .blk.const header { background: #f2f4f7; } .blk.op header { background: #e8f5ec; } .blk.calc header { background: #f3e9f9; }
.blk .t { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 600; }
.blk header button { border: 1px solid var(--border); background: #fff; border-radius: 3px; font-size: 11px; padding: 0 4px; line-height: 16px; }
.blk header button.on { background: var(--accent); color: #fff; border-color: var(--accent); }
.row { display: flex; align-items: center; justify-content: space-between; height: 22px; padding: 0 8px; }
.row.par input, .row.par select { width: 80px; font: inherit; padding: 1px 4px; border: 1px solid var(--border); border-radius: 3px; }
.prev { padding: 2px 8px 0; }
.prev .s { color: var(--muted); font-size: 11px; }
.prev .s.w { color: #93370d; }
.prev .e { color: var(--danger); font-size: 11px; white-space: normal; }
</style>
