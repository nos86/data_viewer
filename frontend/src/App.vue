<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { api, isUnknownHandle, type ChannelOut, type FileOut, type StorageInfo } from './api/client'
import { canvasToBlob, downloadBlob, safeFileName } from './composables/chartImage'
import { buildExportRequest, MissingResult, spreadsheetCsv } from './composables/exportData'
import { decimalSeparator, formatInstant, localeNumber } from './composables/locale'
import { buildTabPdf, type PdfTable } from './composables/pdf'
import { bandTable, cursorTable, STAT_HEADERS } from './composables/tables'
import { useUploads } from './composables/useUploads'
import { newFormula, syncFormulaUid } from './composables/formula'
import { useFormulas } from './composables/useFormulas'
import { aliasesFromChoices, exportTemplate, importTemplate, type Alias, type ImportContext, type ImportResult, type Replacement } from './composables/template'
import { chartMetrics, FONT, tabMargins, type ChartMetrics } from './composables/axes'
import {
  activeCursor, addCursor, centreWindow, clampK, cursorCentreK, cycleActive, kToSeconds, moveCursor, newCursorState, removeCursor, setEdge,
  setSingle, snapK, type CursorState,
} from './composables/cursors'
import { channelFormat } from './composables/format'
import { keyAction, SHORTCUTS, type KeyAction } from './composables/keys'
import {
  addAxis, addChart, addLine, addXYChart, CALC_FILE, isCalc, linesUsingFile, MAX_CHARTS, newLayout, newTab, removeCalcFromLayout, removeFileFromLayout,
  requiredSeries, seriesKey, syncUid, type Chart, type Layout, type Mode,
} from './composables/layout'
import { durationK, extentK, setShiftSeconds, t0OnCursor } from './composables/measures'
import { valueAt, type Placement } from './composables/readout'
import { clearSession, loadSession, saveSession } from './composables/session'
import { useMeasurements } from './composables/useMeasurements'
import BandStats from './components/BandStats.vue'
import ChannelList from './components/ChannelList.vue'
import ExportDialog from './components/ExportDialog.vue'
import UploadPanel from './components/UploadPanel.vue'
import ChartPanel from './components/ChartPanel.vue'
import FormulaEditor from './components/FormulaEditor.vue'
import FormulaList from './components/FormulaList.vue'
import MissingDialog from './components/MissingDialog.vue'
import CursorTable from './components/CursorTable.vue'
import Inspector, { type Catalogue, type Selection } from './components/Inspector.vue'
import MeasurementList from './components/MeasurementList.vue'
import { emptySelector, selectorActive, selectorQuery } from './composables/selector'
import OpenMeasures from './components/OpenMeasures.vue'
import TabBar from './components/TabBar.vue'
import XYChart from './components/XYChart.vue'

const LIST_LIMIT = 500 // GET /files default: the backend returns at most this many, most recent first
const MARKER_LIMIT = 10_000 // visible points above which markers need the user's confirmation (RF-16)

const files = ref<FileOut[]>([])
const { open, runtime, selectedId: selectedMeasureId, stepBaseUs, seriesMap, loading, error, openFile, close, promote, ensureSeries, setSeries, stopPolling } = useMeasurements()
const layout = ref<Layout>(newLayout())
const activeTabId = ref(layout.value.tabs[0].id)
const selection = ref<Selection>(null)
const xWindows = reactive<Record<string, [number, number] | null>>({})
const acceptedMarkers = reactive(new Set<string>()) // accepted for the session (RF-16)
const cursors = reactive<CursorState>(newCursorState())
const showHelp = ref(false)
const bottomOpen = ref(true)
const listFilter = ref('')
const aliases = ref<Alias[]>([])
const templateNotes = ref<string[]>([])
const pendingImport = ref<{ raw: unknown; result: Extract<ImportResult, { ok: true }> } | null>(null)
const templateInput = ref<HTMLInputElement>()
const exportOpen = ref(false)
const exportBusy = ref(false)
const exportError = ref<string | null>(null)
const storage = ref<StorageInfo | null>(null)
const chartRefs = new Map<string, { snapshot(): HTMLCanvasElement } | null>()
const setChartRef = (id: string, el: unknown) => void chartRefs.set(id, (el as { snapshot(): HTMLCanvasElement } | null) ?? null)
const uploadPanel = ref<InstanceType<typeof UploadPanel>>()
let persistEnabled = false

const msg = (e: unknown) => (e instanceof Error ? e.message : String(e))
const activeTab = computed(() => layout.value.tabs.find((t) => t.id === activeTabId.value) ?? layout.value.tabs[0])
const tabLines = computed(() => activeTab.value.charts.flatMap((c) => c.lines))
const anyReady = computed(() => open.value.some((m) => runtime[m.id]?.status?.state === 'done'))

// ---- open measurements, shared axis ---------------------------------------------------------------------

const openLabels = computed(() => Object.fromEntries(open.value.map((m) => [m.id, m.label])))
const labels = computed(() => (open.value.length > 1 ? openLabels.value : null)) // RF-17: tags only when several are open
const shifts = computed(() => Object.fromEntries(open.value.map((m) => [m.id, m.shiftK])))
const slotOf = computed(() => Object.fromEntries(open.value.map((m) => [m.id, m.label])))
const placements = computed<Record<string, Placement>>(() =>
  Object.fromEntries(open.value.map((m) => [m.id, { shiftK: m.shiftK, durK: durationK(m, stepBaseUs.value) }])),
)
const extent = computed(() => extentK(open.value, stepBaseUs.value))
const kb = computed(() => ({ min: extent.value.min, max: extent.value.max }))
const extentSeconds = computed<[number, number]>(() => [kToSeconds(extent.value.min, stepBaseUs.value), kToSeconds(extent.value.max, stepBaseUs.value)])
const channelMap = computed(() => {
  const m = new Map<string, ChannelOut>()
  for (const om of open.value) for (const c of runtime[om.id]?.channels ?? []) m.set(`${om.id}|${c.name}`, c)
  for (const [id, c] of Object.entries(F.info)) m.set(`${CALC_FILE}|${id}`, c) // calculated channels
  return m
})
const catalogue = computed<Catalogue[]>(() => open.value.map((m) => ({ fileId: m.id, label: m.label, name: m.name, channels: runtime[m.id]?.channels ?? [] })))
const t0Us = computed(() => (open.value[0] ? (runtime[open.value[0].id]?.status?.t0_us ?? null) : null)) // start of the primary, UTC µs
const selectedRuntime = computed(() => (selectedMeasureId.value ? runtime[selectedMeasureId.value] : undefined))
const selectedReady = computed(() => selectedRuntime.value?.status?.state === 'done')
const usedChannels = computed(() => new Set(tabLines.value.filter((l) => l.fileId === selectedMeasureId.value).map((l) => l.channel)))
const chartLabels = computed(() => activeTab.value.charts.map((c, i) => (c.kind === 'xy' ? `XY graph ${i + 1}` : `Graph ${i + 1}`)))
const filteredFiles = computed(() => {
  const q = listFilter.value.trim().toLowerCase()
  return q ? files.value.filter((f) => f.name.toLowerCase().includes(q) || (f.vin ?? '').toLowerCase().includes(q)) : files.value
})

// selector (RF-01): VIN, configuration and date range are applied by the catalogue, the name filter below only narrows the shown list
const selector = reactive(emptySelector())
const selectorOn = computed(() => selectorActive(selector))
let selectorTimer = 0
watch(selector, () => {
  window.clearTimeout(selectorTimer)
  selectorTimer = window.setTimeout(() => void refreshFiles(), 400)
})

async function refreshFiles() {
  try {
    files.value = await api.listFiles(selectorQuery(selector))
  } catch (e) {
    error.value = msg(e)
  }
}

const me = ref<{ user?: string | null; adapter: string; version: string } | null>(null)
const identity = computed(() => (me.value ? [me.value.user, me.value.adapter, 'v' + me.value.version].filter(Boolean).join(' · ') : ''))

// ---- uploads (RF-37) ------------------------------------------------------------------------------------
const uploads = useUploads(async (id) => {
  await refreshFiles()
  void refreshStorage()
  const f = files.value.find((x) => x.id === id)
  if (f && open.value.length < 4) await openFromList(f) // opened as soon as it is converted, if a slot is free
})

// ---- storage and heartbeat (RI-39) ------------------------------------------------------------------------
async function refreshStorage() {
  try {
    storage.value = await api.storage()
  } catch {
    storage.value = null
  }
}
const gb = (b: number) => localeNumber(b / 1024 ** 3, 2)
const storageText = computed(() => {
  const s = storage.value
  if (!s) return ''
  return s.limit_bytes ? `Storage ${gb(s.used_bytes)} / ${gb(s.limit_bytes)} GB` : `Storage ${gb(s.used_bytes)} GB`
})
let beat: number | undefined
const heartbeat = () => {
  if (open.value.length) void api.pin(open.value.map((m) => m.id)).catch(() => undefined) // the measurements in use must not be evicted
  void refreshStorage()
}

function resetAnalysis() {
  layout.value = newLayout()
  activeTabId.value = layout.value.tabs[0].id
  selection.value = null
  for (const k of Object.keys(xWindows)) delete xWindows[k]
  acceptedMarkers.clear()
  Object.assign(cursors, newCursorState())
  F.formulas.value = []
  aliases.value = []
}

/** Clicking a file opens it next to the ones already open (max 4); an open one is just selected. */
async function openFromList(f: FileOut) {
  if (!open.value.length) resetAnalysis()
  await openFile(f, 0, refreshFiles)
}

/** When the primary changes, cursors and zoom (expressed in the old primary time) move with it. */
function rebase(delta: number) {
  if (!delta) return
  for (const c of cursors.items) {
    c.k -= delta
    c.k2 -= delta
  }
  const dt = (delta * stepBaseUs.value) / 1e6
  for (const [k, w] of Object.entries(xWindows)) if (w) xWindows[k] = [w[0] - dt, w[1] - dt]
}

function closeMeasurement(id: string) {
  const n = linesUsingFile(layout.value, id)
  if (n && !window.confirm(`Closing this measurement removes ${n} line${n > 1 ? 's' : ''} from the graphs. Continue?`)) return
  removeFileFromLayout(layout.value, id)
  rebase(close(id))
  if (!open.value.length) resetAnalysis()
}

function makePrimary(id: string) {
  rebase(promote(id))
}

function onShift(id: string, seconds: number) {
  const m = open.value.find((x) => x.id === id)
  if (m && m !== open.value[0]) setShiftSeconds(m, seconds, stepBaseUs.value)
}

function setT0(id: string | null = selectedMeasureId.value) {
  const c = activeCur.value
  if (id && c) t0OnCursor(open.value, id, c.k)
}

// ---- data loading: only the visible tab loads series (RI-30) ---------------------------------------------

const requiredKey = computed(() => JSON.stringify(requiredSeries(activeTab.value)))
const doneKey = computed(() => open.value.map((m) => m.id + runtime[m.id]?.status?.state).join(','))
watch([requiredKey, doneKey, activeTabId], () => ensureSeries(requiredSeries(activeTab.value)))

// ---- calculated channels (RF-28 ... RF-33) ------------------------------------------------------------------
const wantedCalc = computed(() => [...new Set(requiredSeries(activeTab.value).filter((r) => r.fileId === CALC_FILE).map((r) => r.channel))])
const F = useFormulas({ open, shifts, ready: doneKey, wanted: wantedCalc, setSeries })
const editFormula = computed(() => (F.editingId.value ? F.formulaMap.value.get(F.editingId.value) : undefined))
const editorMeasures = computed(() => open.value.map((m) => ({ slot: m.label, name: m.name, channels: runtime[m.id]?.channels ?? [] })))
const usedCalc = computed(() => new Set(tabLines.value.filter(isCalc).map((l) => l.channel)))

function newCalc() {
  const f = newFormula(`Formula ${F.formulas.value.length + 1}`)
  F.formulas.value.push(f)
  F.editingId.value = f.id
}
function deleteCalc(id: string) {
  const f = F.formulaMap.value.get(id)
  if (f && !window.confirm(`Delete the calculated channel "${f.name}"? Graph lines that use it are removed.`)) return
  removeCalcFromLayout(layout.value, id)
  F.remove(id)
}
function useCalcInGraph() {
  const f = editFormula.value
  const chart = activeTab.value.charts[0]
  if (f && chart) addChannel(chart, f.id, null, CALC_FILE)
}
watch(
  () => F.formulas.value.map((f) => f.id + f.name).join('|'),
  () => {
    for (const t of layout.value.tabs) for (const c of t.charts) for (const l of c.lines) if (isCalc(l)) l.title = F.formulaMap.value.get(l.channel)?.name ?? l.title
  },
)

// ---- export (RF-35, RF-36) -------------------------------------------------------------------------------
const exportSeriesCount = computed(() => new Set(activeTab.value.charts.flatMap((c) => [...c.lines.map((l) => `${l.fileId}|${l.channel}|${l.mode}`), ...(c.xy?.x ? [`${c.xy.x.fileId}|${c.xy.x.channel}|${c.xy.x.mode}`] : [])])).size)
const exportWindowText = computed(() => {
  const w = xWindow.value
  return w ? `the zoom window ${localeNumber(w[0], 1)} s to ${localeNumber(w[1], 1)} s` : 'the whole measurement'
})

async function runExport(o: { format: 'csv' | 'parquet'; spreadsheet: boolean; filename: string }) {
  exportBusy.value = true
  exportError.value = null
  const build = () =>
    buildExportRequest({
      tab: activeTab.value, labelOf: slotOf.value, multi: open.value.length > 1, shifts: shifts.value,
      handleOf: (id) => F.results[id]?.output?.handle, formulaName: (id) => F.formulaMap.value.get(id)?.name ?? id,
      window: xWindow.value, stepBaseUs: stepBaseUs.value, format: o.format,
      csvLocale: o.spreadsheet ? spreadsheetCsv(decimalSeparator()) : null, filename: o.filename,
    })
  try {
    for (const id of wantedCalc.value) if (!F.results[id]?.output?.handle) await F.evaluate(id)
    let blob: Blob
    try {
      blob = await api.exportData(build())
    } catch (e) {
      if (!isUnknownHandle(e)) throw e
      await Promise.all(wantedCalc.value.map((id) => F.evaluate(id))) // the server forgot a result: compute again, once
      blob = await api.exportData(build())
    }
    downloadBlob(blob, `${safeFileName(o.filename)}.${o.format}`)
    exportOpen.value = false
  } catch (e) {
    exportError.value = e instanceof MissingResult ? 'A calculated channel has no result yet: open it in the editor and fix its errors.' : e instanceof Error ? e.message : String(e)
  } finally {
    exportBusy.value = false
  }
}

async function exportPdf() {
  const ctx = { series: seriesMap.value, channels: channelMap.value, placements: placements.value, labels: labels.value, stepBaseUs: stepBaseUs.value }
  const lines = tabLines.value
  const tables: PdfTable[] = []
  const ct = cursorTable(lines, cursors.items, ctx)
  if (ct.rows.length && ct.columns.length) {
    const clock = (k: number) => (t0Us.value == null ? '' : formatInstant(t0Us.value + k * stepBaseUs.value))
    tables.push({
      title: 'Cursors',
      headers: ['Channel', ...ct.columns.map((c) => `${c.label} ${clock(c.cursor[c.at])}`.trim())],
      rows: ct.rows.map((r) => [(r.tag ? r.tag + ' ' : '') + r.name, ...r.cells.map((c) => c.text)]),
    })
  }
  const band = activeCur.value?.kind === 'band' ? activeCur.value : null
  const bt = bandTable(band, lines, ctx)
  if (band && bt.length)
    tables.push({
      title: `Band ${band.number} statistics`,
      headers: ['Channel', ...STAT_HEADERS, 'Gaps'],
      rows: bt.map((r) => [(r.tag ? r.tag + ' ' : '') + r.name, ...r.values, r.holes ? String(r.holes) : '']),
    })
  const images = activeTab.value.charts.map((c) => chartRefs.get(c.id)?.snapshot()).filter((x): x is HTMLCanvasElement => !!x)
  if (!images.length) {
    error.value = 'There is nothing to put in the PDF yet: add channels to a graph first.'
    return
  }
  try {
    const blob = await buildTabPdf({
      title: open.value.map((m) => (labels.value ? m.label + ': ' : '') + m.name).join('   '),
      subtitle: `Tab "${activeTab.value.name}" · ${formatInstant(Date.now() * 1000, { date: true })}`,
      images,
      tables,
    })
    downloadBlob(blob, `${safeFileName(activeTab.value.name)}.pdf`)
  } catch (e) {
    error.value = `The PDF could not be created: ${e instanceof Error ? e.message : String(e)}`
  }
}
void canvasToBlob

// ---- template file (RF-32, RF-31) -----------------------------------------------------------------------------
function saveTemplate() {
  const t = exportTemplate(layout.value, F.formulas.value, slotOf.value, aliases.value)
  const url = URL.createObjectURL(new Blob([JSON.stringify(t, null, 2)], { type: 'application/json' }))
  const a = document.createElement('a')
  a.href = url
  a.download = 'viewer-template.json'
  a.click()
  URL.revokeObjectURL(url)
}

const importCtx = (): ImportContext => ({
  slots: Object.fromEntries(open.value.map((m) => [m.label, m.id])),
  hasChannel: (fileId, ch) => !!runtime[fileId]?.channels.some((c) => c.name === ch),
  operators: Object.fromEntries(F.operators.value.map((o) => [o.name, { version: o.version, deprecated: o.deprecated }])),
})

function applyImport(r: Extract<ImportResult, { ok: true }>) {
  F.formulas.value = r.formulas
  syncFormulaUid(r.formulas)
  layout.value = r.layout
  syncUid(r.layout)
  activeTabId.value = r.layout.tabs[0].id
  selection.value = null
  templateNotes.value = r.warnings
}

async function onTemplateFile(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  if (!open.value.length) {
    error.value = 'Open the measurements first: template channels are matched to the measurements open as A, B...'
    return
  }
  let raw: unknown
  try {
    raw = JSON.parse(await file.text())
  } catch {
    error.value = 'This file is not valid JSON'
    return
  }
  const r = importTemplate(raw, importCtx())
  if (!r.ok) {
    error.value = r.error
    return
  }
  error.value = null
  aliases.value = r.aliases
  if (r.missing.length) pendingImport.value = { raw, result: r } // RF-31: asked only when something is missing
  else applyImport(r)
}

function confirmMissing(choices: Record<string, Replacement>, remember: string[]) {
  const p = pendingImport.value
  if (!p) return
  const r = importTemplate(p.raw, importCtx(), choices)
  if (!r.ok) {
    error.value = r.error
    pendingImport.value = null
    return
  }
  aliases.value = aliasesFromChoices(choices, new Set(remember), p.result.aliases)
  pendingImport.value = null
  applyImport(r)
}

// ---- layout geometry ------------------------------------------------------------------------------------

let measureCtx: CanvasRenderingContext2D | null = null
const measure = (t: string) => {
  if (!measureCtx) {
    measureCtx = document.createElement('canvas').getContext('2d')!
    measureCtx.font = FONT
  }
  return measureCtx.measureText(t).width
}

/** Full-channel value range of the lines on an axis (auto range ignores the zoom window, RI-26). */
function axisData(chart: Chart, axisId: string): [number, number] {
  let lo = Infinity
  let hi = -Infinity
  for (const l of chart.lines) {
    if (l.axisId !== axisId) continue
    const c = channelMap.value.get(`${l.fileId}|${l.channel}`)
    if (c?.v_min == null || c.v_max == null) continue
    lo = Math.min(lo, c.v_min)
    hi = Math.max(hi, c.v_max)
  }
  return isFinite(lo) ? [lo, hi] : [0, 1]
}

/** XY graphs do not take part in the time-axis alignment (RF-18): only time charts give margins. */
const geometry = computed(() => {
  const byId: Record<string, ChartMetrics> = {}
  for (const c of activeTab.value.charts) if (c.kind === 'time') byId[c.id] = chartMetrics(c.axes, c.divisions, (id) => axisData(c, id), measure)
  return { byId, margin: tabMargins(Object.values(byId)) }
})

const xWindow = computed(() => xWindows[activeTab.value.id] ?? null)
const viewWindow = computed<[number, number]>(() => xWindow.value ?? extentSeconds.value)
const setXWindow = (w: [number, number] | null) => (xWindows[activeTab.value.id] = w)

// ---- legend: fixed-size items so moving a cursor never changes the layout (RI-31) ---------------------------

const legendWidth = computed(() => {
  let name = 0
  let val = 0
  for (const l of tabLines.value) {
    name = Math.max(name, measure((l.title ?? l.channel) + (l.mode === 'linear' ? '' : ' · average')))
    const c = channelMap.value.get(`${l.fileId}|${l.channel}`)
    const f = channelFormat(c?.v_min, c?.v_max)
    val = Math.max(val, measure(f.format(c?.v_min ?? 0)), measure(f.format(c?.v_max ?? 0)))
  }
  return Math.round(Math.min(360, Math.max(170, 30 + name * 1.1 + 26 + val * 1.2 + 40 + (labels.value ? 22 : 0))))
})

const activeCur = computed(() => activeCursor(cursors))

/** Value of the active cursor for every line (RF-26); a band shows the value at its start. */
const readings = computed(() => {
  const out: Record<string, { text: string; off: boolean }> = {}
  const c = activeCur.value
  if (!c) return out
  for (const l of tabLines.value) {
    const s = seriesMap.value.get(seriesKey(l.fileId, l.channel, l.mode))
    if (!s) continue
    const r = valueAt({ n: s.n, jStart: s.jStart, values: s.values }, c.k, placements.value[l.fileId])
    const info = channelMap.value.get(`${l.fileId}|${l.channel}`)
    out[l.id] = { text: r.out ? 'out' : r.value == null ? '—' : channelFormat(info?.v_min, info?.v_max).format(r.value), off: !r.onGrid || !!r.out }
  }
  return out
})

// ---- layout editing -------------------------------------------------------------------------------------

function addChannel(chart: Chart, channel: string, axisId: string | null = null, fileId: string | null = null) {
  const fid = fileId ?? selectedMeasureId.value ?? open.value[0]?.id
  if (!fid) return
  const info = channelMap.value.get(`${fid}|${channel}`)
  const mode: Mode = info?.suggested_mode === 'step' ? 'step' : 'linear'
  if (chart.kind === 'xy' && chart.xy && !chart.xy.x) {
    chart.xy.x = { fileId: fid, channel, mode } // the first channel dropped on an XY graph is its X
    selection.value = { chartId: chart.id, kind: 'chart', id: chart.id }
    return
  }
  const axis = axisId && chart.axes.some((a) => a.id === axisId) ? axisId : undefined
  const line = addLine(activeTab.value, chart, fid, channel, mode, axis)
  if (fid === CALC_FILE) line.title = F.formulaMap.value.get(channel)?.name
  selection.value = { chartId: chart.id, kind: 'line', id: line.id }
}

function addToGraph(channel: string, index: number) {
  const chart = activeTab.value.charts[index]
  if (chart) addChannel(chart, channel)
}

function newGraph(xy = false) {
  const c = xy ? addXYChart(activeTab.value) : addChart(activeTab.value)
  if (c && !xy) addAxis(c, 'left')
  if (c) selection.value = { chartId: c.id, kind: 'chart', id: c.id }
}

function addTab() {
  const t = newTab(`Tab ${layout.value.tabs.length + 1}`)
  layout.value.tabs.push(t)
  activeTabId.value = t.id
}

function removeTab() {
  const tabs = layout.value.tabs
  if (tabs.length <= 1) return
  const i = tabs.findIndex((t) => t.id === activeTabId.value)
  tabs.splice(i, 1)
  activeTabId.value = tabs[Math.max(0, i - 1)].id
  selection.value = null
}

watch(activeTabId, () => (selection.value = null))
watch(() => open.value.length, heartbeat)

function rejectMarker(chart: Chart, lineId: string) {
  const l = chart.lines.find((x) => x.id === lineId)
  if (l) l.marker = false
}

const selectedId = computed(() => (selection.value?.kind === 'chart' ? null : (selection.value?.id ?? null)))

// ---- cursors (RF-21…26) ---------------------------------------------------------------------------------

function newCursor(kind: 'single' | 'band') {
  const w = viewWindow.value
  const centre = snapK((w[0] + w[1]) / 2, stepBaseUs.value)
  const width = Math.max(1, Math.round(snapK(w[1] - w[0], stepBaseUs.value) / 10))
  addCursor(cursors, kind, kind === 'band' ? centre - Math.round(width / 2) : centre, kb.value, width)
}

function onCursorCreate(kind: 'single' | 'band', k: number, k2: number) {
  addCursor(cursors, kind, k, kb.value, k2 - k)
}

function onCursorDrag(id: string, part: 'single' | 'start' | 'end' | 'whole', k: number) {
  const c = cursors.items.find((x) => x.id === id)
  if (!c) return
  if (part === 'single') setSingle(c, k, kb.value)
  else if (part === 'whole') moveCursor(c, k - c.k, kb.value, 'whole')
  else setEdge(c, part, k, kb.value)
}

function goToCursor(id?: string) {
  const c = id ? cursors.items.find((x) => x.id === id) : activeCur.value
  if (!c) return
  setXWindow(centreWindow(viewWindow.value, kToSeconds(cursorCentreK(c), stepBaseUs.value), extentSeconds.value[1]))
}

watch(kb, (b) => {
  if (b.max <= b.min) return // nothing loaded yet
  for (const c of cursors.items) {
    c.k = clampK(c.k, b)
    c.k2 = clampK(c.k2, b)
  }
})

// ---- zoom (RF-19, RF-20) ----------------------------------------------------------------------------------

function zoomBy(factor: number) {
  const [e0, e1] = extentSeconds.value
  if (e1 <= e0) return
  const [a, b] = viewWindow.value
  const c = activeCur.value
  const t = c ? kToSeconds(cursorCentreK(c), stepBaseUs.value) : (a + b) / 2
  const centre = t >= a && t <= b ? t : (a + b) / 2
  const span = Math.min(e1 - e0, Math.max(1, (b - a) * factor))
  const min = Math.max(e0, Math.min(centre - ((centre - a) / (b - a)) * span, e1 - span))
  setXWindow(span >= e1 - e0 ? null : [min, min + span])
}

/** "Apply this zoom to all tabs": single action, otherwise the zoom stays local to the tab. */
function applyZoomToAll() {
  const w = xWindow.value
  for (const t of layout.value.tabs) xWindows[t.id] = w ? [w[0], w[1]] : null
}

// ---- keyboard (RF-39) -------------------------------------------------------------------------------------

function run(a: KeyAction): boolean {
  const tabs = layout.value.tabs
  const ti = tabs.findIndex((t) => t.id === activeTabId.value)
  switch (a.type) {
    case 'moveCursor': {
      const c = activeCur.value
      if (!c) return false
      moveCursor(c, a.dk, kb.value, c.kind === 'band' && a.edge === 'end' ? 'end' : 'whole')
      return true
    }
    case 'zoom': zoomBy(a.factor); return true
    case 'resetZoom': setXWindow(null); return true
    case 'applyZoomAll': applyZoomToAll(); return true
    case 'tab': activeTabId.value = tabs[(ti + a.delta + tabs.length) % tabs.length].id; return true
    case 'tabIndex': if (tabs[a.index]) activeTabId.value = tabs[a.index].id; return !!tabs[a.index]
    case 'newCursor': if (anyReady.value) newCursor('single'); return anyReady.value
    case 'newBand': if (anyReady.value) newCursor('band'); return anyReady.value
    case 'removeCursor': if (activeCur.value) removeCursor(cursors, activeCur.value.id); return !!activeCur.value
    case 'cycleCursor': cycleActive(cursors, a.dir); return cursors.items.length > 0
    case 'goToCursor': goToCursor(); return !!activeCur.value
    case 'setT0': setT0(); return !!activeCur.value
    case 'help': showHelp.value = !showHelp.value; return true
  }
}

function onKey(e: KeyboardEvent) {
  const a = keyAction(e)
  if (a && run(a)) e.preventDefault()
}

// ---- session (RI-32) --------------------------------------------------------------------------------------

let persistTimer: number | undefined
function persist() {
  if (!persistEnabled) return
  window.clearTimeout(persistTimer)
  persistTimer = window.setTimeout(() => {
    saveSession({
      schema: 3,
      measurements: open.value.map((m) => ({ id: m.id, shiftK: m.shiftK })),
      layout: layout.value,
      formulas: F.formulas.value,
      aliases: aliases.value,
      activeTabId: activeTabId.value,
      cursors,
      xWindows: { ...xWindows },
      acceptedMarkers: [...acceptedMarkers],
      selectedMeasureId: selectedMeasureId.value,
    })
  }, 150)
}
watch([layout, activeTabId, () => acceptedMarkers.size, open, selectedMeasureId, F.formulas, aliases], persist, { deep: true })
watch(cursors, persist, { deep: true })
watch(xWindows, persist, { deep: true })

function resetSession() {
  persistEnabled = false
  window.clearTimeout(persistTimer)
  clearSession()
  window.location.reload()
}

onMounted(async () => {
  window.addEventListener('keydown', onKey)
  void F.loadOperators()
  void refreshStorage()
  void api.me().then((m) => (me.value = m)).catch(() => undefined)
  beat = window.setInterval(heartbeat, 60_000)
  await refreshFiles()
  const s = loadSession()
  const present = s ? s.measurements.filter((m) => files.value.some((f) => f.id === m.id)) : []
  if (s && present.length) {
    // reopen by id: the Parquet cache answers 'done' without converting again
    layout.value = s.layout
    syncUid(s.layout)
    const gone = s.measurements.filter((m) => !present.some((p) => p.id === m.id))
    for (const m of gone) removeFileFromLayout(layout.value, m.id)
    if (gone.length) error.value = `${gone.length} measurement${gone.length > 1 ? 's are' : ' is'} no longer available (removed by the storage limit). Upload ${gone.length > 1 ? 'them' : 'it'} again.`
    F.formulas.value = s.formulas
    syncFormulaUid(s.formulas)
    aliases.value = s.aliases
    activeTabId.value = s.activeTabId
    Object.assign(cursors, s.cursors)
    for (const [k, v] of Object.entries(s.xWindows)) xWindows[k] = v
    s.acceptedMarkers.forEach((m) => acceptedMarkers.add(m))
    for (const m of present) await openFile(files.value.find((f) => f.id === m.id)!, m.shiftK, refreshFiles)
    selectedMeasureId.value = s.selectedMeasureId ?? open.value[0]?.id ?? null
  }
  persistEnabled = true
})
onBeforeUnmount(() => {
  window.clearInterval(beat)
  stopPolling()
  window.removeEventListener('keydown', onKey)
})
</script>

<template>
  <div class="layout">
    <aside class="side">
      <section>
        <header class="row"><h2>Measurements</h2><button @click="refreshFiles">Refresh</button></header>
        <UploadPanel ref="uploadPanel" :items="uploads.items" @pick="uploads.start" @cancel="uploads.cancel" @dismiss="uploads.dismiss" />
        <input v-model="listFilter" type="search" class="filter" placeholder="Filter by name…" />
        <div class="selector" aria-label="Selector">
          <input v-model="selector.vin" type="search" placeholder="VIN" />
          <input v-model="selector.configuration" type="search" placeholder="Configuration" />
          <label>From <input v-model="selector.from" type="date" /></label>
          <label>To <input v-model="selector.to" type="date" /></label>
          <button v-if="selectorOn" class="link" @click="Object.assign(selector, emptySelector())">Clear selection</button>
        </div>
        <MeasurementList :files="filteredFiles" :open-ids="openLabels" :filtered="selectorOn || !!listFilter" :capped="files.length >= LIST_LIMIT" @open="openFromList" />
      </section>
      <section v-if="open.length">
        <h2>Open ({{ open.length }}/4)</h2>
        <OpenMeasures
          :list="open"
          :runtime="runtime"
          :selected-id="selectedMeasureId"
          :step-base-us="stepBaseUs"
          :can-t0="!!activeCur"
          @select="selectedMeasureId = $event"
          @close="closeMeasurement"
          @primary="makePrimary"
          @shift="onShift"
          @t0="setT0($event)"
        />
      </section>
      <section v-if="open.length">
        <FormulaList
          :formulas="F.formulas.value"
          :results="F.results"
          :busy="F.busy"
          :charts="chartLabels"
          :used="usedCalc"
          :can-add="open.length > 0"
          @create="newCalc"
          @edit="F.editingId.value = $event"
          @remove="deleteCalc"
          @add="(id, i) => activeTab.charts[i] && addChannel(activeTab.charts[i], id, null, CALC_FILE)"
        />
      </section>
      <section v-if="selectedRuntime && selectedMeasureId" class="grow">
        <h2>Channels{{ labels ? ' · ' + openLabels[selectedMeasureId] : '' }}</h2>
        <div v-if="selectedRuntime.status && selectedRuntime.status.state !== 'done'" class="status">
          <div v-if="selectedRuntime.status.state === 'error'" class="err">{{ selectedRuntime.status.message }}</div>
          <template v-else>
            <div>Converting: {{ selectedRuntime.status.phase || 'queued' }}</div>
            <progress :value="selectedRuntime.status.progress" max="1" />
          </template>
        </div>
        <div v-if="selectedRuntime.status?.warnings?.length" class="warn">
          <div v-for="w in selectedRuntime.status.warnings" :key="w">{{ w }}</div>
        </div>
        <ChannelList
          :file-id="selectedMeasureId"
          :channels="selectedRuntime.channels"
          :complete="selectedRuntime.complete"
          :used="usedChannels"
          :charts="chartLabels"
          :disabled="!selectedReady"
          @add="addToGraph"
        />
      </section>
      <div v-if="identity" class="foot" title="Signed-in user, adapter and version">{{ identity }}</div>
      <div v-if="storageText" class="foot" :title="storage ? `${gb(storage.uploads_bytes)} GB uploads, ${gb(storage.parquet_bytes)} GB converted data` : ''">{{ storageText }}</div>
    </aside>

    <main class="main">
      <div v-if="error" class="err banner">{{ error }}</div>
      <div v-if="!open.length" class="empty">Open a measurement to start.</div>
      <template v-else>
        <div class="toolbar">
          <strong>{{ open.map((m) => (labels ? m.label + ' ' : '') + m.name).join('  ·  ') }}</strong>
          <button :disabled="activeTab.charts.length >= MAX_CHARTS" @click="newGraph(false)">Add graph</button>
          <button :disabled="activeTab.charts.length >= MAX_CHARTS" title="X–Y graph: channels plotted against another channel" @click="newGraph(true)">Add XY graph</button>
          <span class="sep"></span>
          <button :disabled="!anyReady" title="New cursor at the centre of the view (N)" @click="newCursor('single')">Cursor</button>
          <button :disabled="!anyReady" title="New band (B)" @click="newCursor('band')">Band</button>
          <button :disabled="!activeCur" title="Centre the view on the active cursor (G)" @click="goToCursor()">Go to cursor</button>
          <span class="sep"></span>
          <button :disabled="!anyReady" title="Copy this tab's zoom to every tab (A)" @click="applyZoomToAll">Apply zoom to all tabs</button>
          <button :disabled="!anyReady" title="Reset zoom (0)" @click="setXWindow(null)">Reset zoom</button>
          <span v-if="loading" class="muted">Loading series…</span>
          <span class="sep"></span>
          <button :disabled="!anyReady" title="Save the data of this tab as CSV or Parquet" @click="exportError = null; exportOpen = true">Export data…</button>
          <button :disabled="!anyReady" title="Save this tab as a PDF: graphs, cursor table, band statistics" @click="exportPdf">Tab as PDF</button>
          <button title="Save the layout and the calculated channels as a JSON file" @click="saveTemplate">Save template</button>
          <button title="Load a template file onto the open measurements" @click="templateInput?.click()">Load template</button>
          <input ref="templateInput" type="file" accept="application/json,.json" hidden @change="onTemplateFile" />
          <span class="hint">
            <button class="link" title="Keyboard shortcuts (?)" @click="showHelp = !showHelp">Shortcuts</button>
            <button class="link" title="Forget layout, cursors and zoom of this browser tab" @click="resetSession">Reset session</button>
          </span>
        </div>
        <div v-if="templateNotes.length" class="banner note">
          <button class="link" @click="templateNotes = []">dismiss</button>
          <div v-for="n in templateNotes" :key="n">{{ n }}</div>
        </div>
        <div v-if="showHelp" class="help">
          <table>
            <tr v-for="[k, d] in SHORTCUTS" :key="k"><td><kbd>{{ k }}</kbd></td><td>{{ d }}</td></tr>
          </table>
          <p>Mouse: drag = zoom · wheel = zoom at pointer · Shift+drag = pan · double-click = reset · drag a cursor line or its number to move it. XY graph: drag a rectangle to zoom.</p>
        </div>
        <TabBar :tabs="layout.tabs" :active-id="activeTab.id" @select="activeTabId = $event" @add="addTab" />
        <div class="charts">
          <template v-for="(c, i) in activeTab.charts" :key="c.id">
            <XYChart
              v-if="c.kind === 'xy'"
              :ref="(el) => setChartRef(c.id, el)"
              :tab-name="activeTab.name"
              :index="i"
              :chart="c"
              :series="seriesMap"
              :shifts="shifts"
              :channels="channelMap"
              :cursors="cursors.items"
              :active-cursor-id="cursors.activeId"
              :labels="labels"
              :selected-id="selectedId"
              :legend-width="legendWidth"
              @select-line="selection = { chartId: c.id, kind: 'line', id: $event }"
              @select-chart="selection = { chartId: c.id, kind: 'chart', id: c.id }"
              @drop-channel="(ch, _ax, fid) => addChannel(c, ch, null, fid)"
            />
            <ChartPanel
              v-else
              :ref="(el) => setChartRef(c.id, el)"
              :tab-name="activeTab.name"
              :index="i"
              :chart="c"
              :metrics="geometry.byId[c.id]"
              :margin="geometry.margin"
              :series="seriesMap"
              :x-window="xWindow"
              :extent="extentSeconds"
              :shifts="shifts"
              :labels="labels"
              :step-base-us="stepBaseUs"
              :marker-limit="MARKER_LIMIT"
              :accepted-markers="acceptedMarkers"
              :selected-id="selectedId"
              :cursors="cursors.items"
              :active-cursor-id="cursors.activeId"
              :kb="kb"
              :readings="readings"
              :legend-width="legendWidth"
              @xchange="setXWindow"
              @select-line="selection = { chartId: c.id, kind: 'line', id: $event }"
              @select-axis="selection = { chartId: c.id, kind: 'axis', id: $event }"
              @select-chart="selection = { chartId: c.id, kind: 'chart', id: c.id }"
              @drop-channel="(ch, ax, fid) => addChannel(c, ch, ax, fid)"
              @marker-accept="acceptedMarkers.add($event)"
              @marker-reject="rejectMarker(c, $event)"
              @cursor-activate="cursors.activeId = $event"
              @cursor-drag="onCursorDrag"
              @cursor-create="onCursorCreate"
              @cursor-go-to="goToCursor($event)"
            />
          </template>
        </div>
        <section class="bottom">
          <header class="row">
            <h2>Cursors · {{ activeTab.name }}</h2>
            <button class="link" @click="bottomOpen = !bottomOpen">{{ bottomOpen ? 'Hide' : 'Show' }}</button>
          </header>
          <div v-if="bottomOpen" class="panes">
            <CursorTable
              :lines="tabLines"
              :series="seriesMap"
              :channels="channelMap"
              :cursors="cursors.items"
              :active-id="cursors.activeId"
              :step-base-us="stepBaseUs"
              :placements="placements"
              :labels="labels"
              :t0-us="t0Us"
              @activate="cursors.activeId = $event"
            />
            <BandStats :band="activeCur" :lines="tabLines" :series="seriesMap" :channels="channelMap" :step-base-us="stepBaseUs" :placements="placements" :labels="labels" />
          </div>
        </section>
      </template>
    </main>

    <ExportDialog
      v-if="exportOpen"
      :tab-name="activeTab.name"
      :series-count="exportSeriesCount"
      :window-text="exportWindowText"
      :decimal="decimalSeparator()"
      :busy="exportBusy"
      :error="exportError"
      @confirm="runExport"
      @cancel="exportOpen = false"
    />
    <FormulaEditor
      v-if="editFormula"
      :formula="editFormula"
      :formulas="F.formulaMap.value"
      :operators="F.operators.value"
      :measures="editorMeasures"
      :result="F.results[editFormula.id]"
      :busy="!!F.busy[editFormula.id]"
      @close="F.editingId.value = null"
      @use-in-graph="useCalcInGraph"
    />
    <MissingDialog
      v-if="pendingImport"
      :missing="pendingImport.result.missing"
      :measures="editorMeasures"
      :formulas="pendingImport.result.formulas.map((f) => ({ id: f.id, name: f.name }))"
      :warnings="pendingImport.result.warnings"
      @confirm="confirmMissing"
      @cancel="pendingImport = null"
    />
    <aside v-if="open.length" class="props">
      <h2>Properties</h2>
      <Inspector :tab="activeTab" :selection="selection" :channels="channelMap" :catalogue="catalogue" :multi="open.length > 1" @select="selection = $event" @remove-tab="removeTab" />
    </aside>
  </div>
</template>

<style scoped>
.layout { display: grid; grid-template-columns: 300px 1fr auto; height: 100%; }
.side { border-right: 1px solid var(--border); background: var(--panel); display: flex; flex-direction: column; min-height: 0; padding: 12px; gap: 12px; overflow: auto; }
.side section.grow { flex: 1; min-height: 240px; display: flex; flex-direction: column; }
.filter { margin-bottom: 6px; }
.selector { display: grid; grid-template-columns: 1fr 1fr; gap: 4px; margin-bottom: 6px; }
.selector label { font-size: 11px; color: var(--muted); display: flex; flex-direction: column; gap: 2px; }
.selector .link { grid-column: 1 / -1; text-align: left; }
.foot { font-size: 11px; color: var(--muted); margin-top: auto; padding-top: 4px; }
.props { border-left: 1px solid var(--border); background: var(--panel); padding: 12px; width: 250px; overflow: auto; }
h2 { margin: 0 0 8px; font-size: 13px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); }
.row { display: flex; justify-content: space-between; align-items: center; }
.main { display: flex; flex-direction: column; min-width: 0; min-height: 0; padding: 12px; gap: 8px; }
.toolbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.sep { width: 1px; height: 18px; background: var(--border); }
.hint { margin-left: auto; display: flex; gap: 10px; }
.link { border: none; background: none; color: var(--accent); padding: 0; font-size: 12px; }
.charts { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 6px; }
.bottom { border: 1px solid var(--border); border-radius: 6px; padding: 6px 8px; max-height: 210px; display: flex; flex-direction: column; min-height: 0; }
.bottom h2 { margin: 0; }
.panes { display: flex; gap: 24px; overflow: auto; min-height: 0; padding-top: 4px; }
.help { border: 1px solid var(--border); border-radius: 6px; padding: 8px 12px; background: var(--panel); font-size: 12px; }
.help td { padding: 1px 12px 1px 0; }
kbd { background: var(--bg); border: 1px solid var(--border); border-radius: 3px; padding: 0 4px; font-size: 11px; }
.empty { margin: auto; color: var(--muted); }
.muted { color: var(--muted); }
.err { color: var(--danger); }
.banner { padding: 8px 10px; background: #fee4e2; border-radius: 6px; }
.warn { font-size: 12px; color: #93370d; margin-bottom: 6px; }
.note { background: #fef0c7; color: #93370d; font-size: 12px; }
.status { font-size: 12px; margin-bottom: 8px; }
progress { width: 100%; }
</style>
