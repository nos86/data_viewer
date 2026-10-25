<script setup lang="ts">
/** Replace missing channels (RF-31): shown only when a loaded template refers to channels that are not available. */
import { reactive, ref } from 'vue'
import type { ChannelOut } from '../api/client'
import type { MissingRef, Replacement } from '../composables/template'

const props = defineProps<{
  missing: MissingRef[]
  measures: { slot: string; name: string; channels: ChannelOut[] }[]
  formulas: { id: string; name: string }[]
  warnings: string[]
}>()
const emit = defineEmits<{ confirm: [choices: Record<string, Replacement>, remember: string[]]; cancel: [] }>()

type Kind = 'ch' | 'const' | 'calc' | 'skip'
interface Row { kind: Kind; slot: string; channel: string; value: number; formulaId: string; remember: boolean }
const rows = reactive<Record<string, Row>>(
  Object.fromEntries(
    props.missing.map((m) => [
      m.key,
      { kind: props.measures.length ? 'ch' : 'skip', slot: props.measures[0]?.slot ?? '', channel: props.measures[0]?.channels[0]?.name ?? '', value: 0, formulaId: props.formulas[0]?.id ?? '', remember: false } as Row,
    ]),
  ),
)
const channelsOf = (slot: string) => props.measures.find((m) => m.slot === slot)?.channels ?? []
const error = ref('')

function confirm() {
  const choices: Record<string, Replacement> = {}
  const remember: string[] = []
  for (const m of props.missing) {
    const r = rows[m.key]
    if (r.kind === 'ch') {
      if (!r.channel) return void (error.value = `Choose a channel to replace ${m.slot}: ${m.channel}`)
      choices[m.key] = { kind: 'ch', slot: r.slot, channel: r.channel }
    } else if (r.kind === 'const') choices[m.key] = { kind: 'const', value: Number(r.value) }
    else if (r.kind === 'calc') {
      if (!r.formulaId) return void (error.value = `Choose a calculated channel to replace ${m.slot}: ${m.channel}`)
      choices[m.key] = { kind: 'calc', formulaId: r.formulaId }
    } else choices[m.key] = { kind: 'skip' }
    if (r.remember && r.kind !== 'skip') remember.push(m.key)
  }
  emit('confirm', choices, remember)
}
</script>

<template>
  <div class="overlay" @keydown.stop @keyup.stop>
    <div class="dlg">
      <h3>Missing channels</h3>
      <p class="muted">
        The template uses channels that are not available in the open measurements. Replace each with another channel, a constant or a calculated
        channel, or leave it out. Nothing is remembered unless you tick the box.
      </p>
      <ul v-if="warnings.length" class="warn"><li v-for="w in warnings" :key="w">{{ w }}</li></ul>
      <div v-for="m in missing" :key="m.key" class="item">
        <div class="head"><strong>{{ m.slot }}: {{ m.channel }}</strong> <span class="muted">used in {{ m.usedBy.join(' · ') }}</span></div>
        <div class="choice">
          <label><input v-model="rows[m.key].kind" type="radio" value="ch" :disabled="!measures.length" /> Channel</label>
          <template v-if="rows[m.key].kind === 'ch'">
            <select v-model="rows[m.key].slot" @change="rows[m.key].channel = channelsOf(rows[m.key].slot)[0]?.name ?? ''">
              <option v-for="x in measures" :key="x.slot" :value="x.slot">{{ x.slot }} · {{ x.name }}</option>
            </select>
            <select v-model="rows[m.key].channel"><option v-for="c in channelsOf(rows[m.key].slot)" :key="c.name" :value="c.name">{{ c.name }}</option></select>
          </template>
        </div>
        <div class="choice">
          <label><input v-model="rows[m.key].kind" type="radio" value="const" /> Constant</label>
          <input v-if="rows[m.key].kind === 'const'" v-model.number="rows[m.key].value" type="number" step="any" />
        </div>
        <div class="choice">
          <label><input v-model="rows[m.key].kind" type="radio" value="calc" :disabled="!formulas.length" /> Calculated channel</label>
          <select v-if="rows[m.key].kind === 'calc'" v-model="rows[m.key].formulaId"><option v-for="f in formulas" :key="f.id" :value="f.id">ƒ {{ f.name }}</option></select>
        </div>
        <div class="choice"><label><input v-model="rows[m.key].kind" type="radio" value="skip" /> Leave it out</label></div>
        <label v-if="rows[m.key].kind !== 'skip'" class="rem"><input v-model="rows[m.key].remember" type="checkbox" /> Remember as alias in the next saved template</label>
      </div>
      <p v-if="error" class="err">{{ error }}</p>
      <footer>
        <button @click="emit('cancel')">Cancel</button>
        <button class="primary" @click="confirm">Apply</button>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.overlay { position: fixed; inset: 0; background: rgba(16, 24, 40, 0.45); z-index: 60; display: grid; place-items: center; padding: 24px; }
.dlg { background: var(--bg); border-radius: 10px; width: min(640px, 100%); max-height: 100%; overflow: auto; padding: 16px 20px; border: 1px solid var(--border); }
h3 { margin: 0 0 6px; }
.muted { color: var(--muted); font-size: 12px; }
.warn { font-size: 12px; color: #93370d; padding-left: 18px; }
.item { border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; margin: 8px 0; display: flex; flex-direction: column; gap: 4px; }
.head { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
.choice { display: flex; gap: 8px; align-items: center; min-height: 24px; }
.choice label { min-width: 150px; }
.choice select, .choice input[type='number'] { font: inherit; padding: 2px 6px; border: 1px solid var(--border); border-radius: 4px; }
.rem { font-size: 12px; color: var(--muted); margin-top: 2px; }
.err { color: var(--danger); font-size: 12px; }
footer { display: flex; justify-content: flex-end; gap: 8px; margin-top: 8px; }
.primary { background: var(--accent); color: #fff; border: none; border-radius: 6px; padding: 5px 14px; }
</style>
