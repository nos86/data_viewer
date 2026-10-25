<script setup lang="ts">
import { computed } from 'vue'
import type { ChannelOut, SeriesData } from '../api/client'
import type { Cursor } from '../composables/cursors'
import { localize } from '../composables/locale'
import type { Line } from '../composables/layout'
import type { Placement } from '../composables/readout'
import { bandTable, STAT_HEADERS } from '../composables/tables'

const props = defineProps<{
  band: Cursor | null // active cursor, shown only if it is a band
  lines: Line[]
  series: Map<string, SeriesData>
  channels: Map<string, ChannelOut>
  stepBaseUs: number
  placements: Record<string, Placement>
  labels: Record<string, string> | null
}>()

const rows = computed(() => bandTable(props.band, props.lines, { series: props.series, channels: props.channels, placements: props.placements, labels: props.labels, stepBaseUs: props.stepBaseUs }))
const duration = computed(() => (props.band ? ((props.band.k2 - props.band.k) * props.stepBaseUs) / 1e6 : 0))
</script>

<template>
  <div class="wrap">
    <p v-if="!band || band.kind !== 'band'" class="none">Select a band cursor (button Band, key B, or Alt+drag on a graph) to see its statistics.</p>
    <template v-else>
      <div class="head">Band {{ band.number }} · duration {{ localize(duration.toFixed(1)) }} s</div>
      <table>
        <thead>
          <tr><th class="ch">Channel</th><th v-for="h in STAT_HEADERS" :key="h">{{ h }}</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.line.id">
            <td class="ch"><span class="sw" :style="{ background: r.line.color }"></span><i v-if="r.tag" class="lab">{{ r.tag }}</i>{{ r.name }}</td>
            <td v-for="(v, i) in r.values" :key="i">{{ v }}</td>
            <td>
              <span v-if="r.holes" class="gap" :title="`${r.holes} of ${r.points} grid points in the band are missing (acquisition gap)`">⚠ {{ r.holes }} gap{{ r.holes > 1 ? 's' : '' }}</span>
            </td>
          </tr>
        </tbody>
      </table>
    </template>
  </div>
</template>

<style scoped>
.wrap { overflow: auto; }
.head { font-size: 12px; font-weight: 600; margin-bottom: 4px; }
table { border-collapse: collapse; font-size: 12px; font-variant-numeric: tabular-nums; }
th, td { padding: 2px 10px; text-align: right; border-bottom: 1px solid var(--border); white-space: nowrap; }
th { background: var(--panel); position: sticky; top: 0; }
.ch { text-align: left; }
.sw { display: inline-block; width: 8px; height: 8px; border-radius: 2px; margin-right: 6px; }
.lab { font-style: normal; font-size: 10px; font-weight: 700; background: #e4e7ec; color: #344054; border-radius: 3px; padding: 0 4px; margin-right: 4px; }
.gap { color: #93370d; font-weight: 600; }
.none { margin: 6px 0; color: var(--muted); font-size: 12px; }
</style>
