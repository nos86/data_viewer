# Measurement viewer

Web app to view and analyse measurement acquisitions (FastAPI backend, Vue 3 + TypeScript frontend).
Full specification: [docs/requirements.md](docs/requirements.md). **Status: step 7 of 7 (last)** — the same app runs on a PC and as a Databricks App: measurements from the metadata table and the KVT (Delta), selector by VIN / configuration / date,
per-user permissions, `pip install` + `viewer serve`, Azure DevOps pipeline, structured logs. On top of upload, export, storage limit, palette and local formats (step 6),
layout, cursors, several measurements, XY graphs, calculated channels, templates and a session that survives a refresh.

## Repository

```
backend/    Python package `viewer` (pyproject.toml)
  src/viewer/core/       grid maths (numpy, no I/O), data classes            RI-01
  src/viewer/ports.py    the four interfaces as typing.Protocol              RI-33
  src/viewer/adapters/   local (filesystem, DuckDB, Delta), databricks, in-memory (tests)   RI-33/34/36
  src/viewer/cli.py      `viewer serve | mock | version`                      RI-41
  src/viewer/service/    ingestion wrapper, catalogue, series use cases
  src/viewer/api/        thin FastAPI endpoints under /api/v1                RI-37
  src/viewer/mock/       deterministic mock data generator
  src/viewer/extractor_placeholder.py   extract(path) -> (DataFrame, dict)
  tests/ benchmarks/ scripts/
frontend/   Vue 3 + TS + Vite + uPlot
scripts/dev.py          one command to start everything (development)
scripts/                build_frontend.py, release_check.py, databricks_smoke.py
azure-pipelines*.yml, databricks.yml, app.yaml, requirements.txt    CI/CD and Databricks App   RI-43
docs/                   requirements.md, benchmarks.md, databricks.md
```

## Setup

Requires Python ≥ 3.11 and Node ≥ 20.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e "backend[dev]"      # Windows; on Linux/macOS: .venv/bin/python
cd frontend && npm install && cd ..
```

## Install as a package and run (RI-41)

The wheel contains the built frontend, so one install and one command are enough:

```bash
pip install viewer-<version>-py3-none-any.whl            # from the pipeline artifact; extras: [delta] [databricks]
viewer mock                                              # sample measurements in ./data/source (optional)
viewer serve                                             # http://127.0.0.1:8000, opens the browser (--no-browser, --host, --port)
```

From a checkout: `python scripts/build_frontend.py && python -m build backend` builds the wheel (the version comes from the git tag `vMAJOR.MINOR.PATCH`; `0.0.x.devN` without tags).
`viewer mock --kvt` writes a metadata table and a KVT as local Delta folders (needs the `delta` extra), to try the selector without a workspace:

```bash
viewer mock --kvt --out data/delta
VIEWER_METADATA_DIR=data/delta/metadata VIEWER_KVT_DIR=data/delta/kvt viewer serve
```

Databricks: see [docs/databricks.md](docs/databricks.md) (deployment, permissions, what is not verified).

## Generate mock data

Measurements are fake `.bin` files (a small JSON with seed and parameters) expanded by the mock into the
DataFrame the real extractor will return. They go to `VIEWER_SOURCE_DIR` (default `data/source`).

```bash
.venv/Scripts/python -m viewer.mock.generate            # tiny, small (~1M samples), DST (25/10/2026 fall-back)
.venv/Scripts/python -m viewer.mock.generate --large    # + 50M samples over 3 h (benchmark)
```

Content: channels at 100, 10, 5, 2, 1 and 3 Hz (3 Hz is outside the n chain), timestamp jitter, event channels,
isolated missing samples and long interruptions, a boolean and a non-numeric channel (the latter is excluded and
reported), duplicated timestamps. The `large` profile is float64 only (decision for the benchmark).

## Run

```bash
.venv/Scripts/python scripts/dev.py
```

Starts the backend (http://127.0.0.1:8000, API docs at `/docs`) and the frontend (http://127.0.0.1:5173) with reload.
It generates the mock files on first run. Stop with Ctrl+C.

## What to try

1. Open http://127.0.0.1:5173 and click `mock_small.bin` (it converts in a separate process; the catalogue shows native → assigned Hz).
2. **Add channels** by dragging them from the list onto a graph (or onto one of its axes), or with the `+` menu on each row.
   The first channel creates a full-height axis Y1; the dot in the list marks channels used in the current tab.
3. **Graphs and tabs**: "Add graph" (max 4 per tab, stacked, plot areas aligned), `+` next to the tabs for a new tab.
   Click a graph, an axis or a legend entry to edit it in *Properties*.
4. **Axes on bands**: select a line → *Axis → New axis…*; then click the axis in the margin and set *Band from/to*
   (divisions counted from the bottom of the graph), side, and manual min/max (empty = automatic: range of the whole channel).
   Ticks and labels appear only inside the band; overlapping bands on one side get separate lanes, adjacent bands share a lane.
   *Properties → graph → Vertical divisions* changes the grid (default 10).
5. **Mode per line**: step / linear / average (average is not offered for event channels). Colour, width, solid/dashed/dotted.
6. **Markers**: tick *Markers on all points*; with more than 10,000 visible points per line a banner asks to draw them (accepted for the
   session) or turn them off.
7. Zoom/pan: drag = zoom, wheel = zoom at the pointer, Shift+drag = pan, double-click = reset. All graphs of a tab follow the same window.
   Zoom is **local to the tab**; *Apply zoom to all tabs* (key `A`) copies it to every tab in one action.
8. **Cursors** (global: the same on every graph of every tab, positions are integer steps of 0.1 s): toolbar *Cursor* / *Band*
   (or keys `N` / `B`) create one at the centre of the view; **Alt+click** on a graph creates a cursor, **Alt+drag** a band. Drag a line,
   a band edge or the number badge on the time axis to move it (it snaps to the 0.1 s lattice); click a badge to make it active.
   A cursor outside the view shows an arrow with its number on the border; click it or press `G` / *Go to cursor* to recentre.
9. **Values**: the legend of every graph shows the value of the active cursor (band: its start); the *Cursors* panel lists all channels of the
   current tab against every cursor (band: start and end). Italic = the cursor lies between grid points of that channel, so the value of the
   previous grid point is shown (e.g. a 1 Hz channel read at 0.4 s).
10. **Band statistics** (select a band): min, max, mean, start, end, Δ and duration per channel, with `⚠ N gaps` when acquisition holes fall
    inside the band (try `f1_000` between 1740 s and 1800 s).
11. **Keyboard**: `←/→` move the active cursor one step, `Shift` ten, `Alt+←/→` the end of a band, `[` `]` previous/next cursor, `Delete`
    remove, `+` `-` `0` zoom, `PageUp/PageDown` or `Alt+1…9` tabs. `?` shows the list.
12. **Several measurements**: click a second file in the list (up to 4 open; the first one is the *primary* and gives the cursor time). Each open
    measurement has a label (A, B…) shown on its legend entries, table rows and statistics. *Shift (s)* in the *Open* panel moves a measurement on the
    shared axis (rounded to 0.1 s, the applied value is shown, negative values allowed; the axis grows to cover the union). **T** or *t0 → cursor*
    moves the start of the selected (non-primary) measurement to the active cursor. *Make primary* changes the reference measurement (cursors and zoom follow).
    Where a measurement is not running a cursor reads **out of measure** (never the last value). The channel list shows the selected measurement
    (click its row); dragging from it uses that measurement.
13. **XY graph**: *Add XY graph* takes one of the 4 slots of the tab. Drop a channel on it: the first one is the **X** channel (change it in
    *Properties*), the next ones are Y lines (each can come from a different open measurement). Points are the times X and Y have in common
    (the faster channel is sub-sampled by index; holes skip the point; nothing is interpolated). A cursor is a highlighted numbered point, a band
    thickens the stretch of the path inside it. Drag a rectangle to zoom, double-click to reset. A pair whose shifts leave no common grid points
    shows "no common times".
14. **Calculated channels** (*Calculated channels → New*): a block editor opens. Add blocks from the palette: a *channel* of an open measurement
    (the slot A/B… names the measurement), a *constant*, an *operator*, or another *calculated channel*. Drag from the dot on the right of a block
    to an input dot on the left of an operator; double-click a link to remove it; a link that would close a loop is refused. Each block shows its
    **intermediate result** (points, min…max, nulls and a small curve), or its error in red. `out` marks the block that is the result;
    *Use in graph* (or the `+` menu / drag from the list) puts it on a graph, where it carries an `ƒ` tag. Operators come from the backend
    (`GET /api/v1/operators`): `add sub mul div neg abs sqrt pow min max clip`, `gt lt ge le eq` (0/1), `and or not`, `select`, over time
    `derivative integral moving_average delay`, and `magnitude` (a formula promoted to an operator).
    Rules worth knowing: the result lives on the slowest grid among the channel inputs (faster channels are sub-sampled by index, only measured
    grid values), shifted measurements meet only at coincident points (a block warns when there are none), nulls propagate, constants cannot feed
    an operator alone or a temporal operator.
15. **Template files**: *Save template* downloads a JSON with the layout and the formulas (not the measurements, shifts, cursors or zoom; the backend
    keeps nothing). *Load template* applies it to the measurements that are open **under the same labels** (A, B…). If a channel is not available,
    a *Missing channels* dialog (only then) lets you replace it with another channel, a constant or a calculated channel, or leave it out;
    tick *Remember as alias* to store the replacement in the next saved template, where it is applied silently later.
16. **Upload a measurement** (*Upload…* or drop files on the measurement list): the file is sent in blocks (50 MB by default), each checked with a
    SHA-256, with progress *Uploading % → Converting (phase) → Ready*; it opens as soon as it is converted. If the connection drops, select the
    **same file again**: only the missing blocks are sent. An identical file that is already there is recognised by its content and not converted twice.
    Problems are reported in plain words: file larger than the limit, damaged block, file the extractor does not recognise (with the cause).
17. **Export**: *Export data…* saves what the current tab shows (its channels, its zoom window) as **CSV or Parquet** in the long format
    `time_s, channel, value`; each channel keeps its own grid and gaps produce no row. With an Italian/German… browser language the CSV option
    *Spreadsheet friendly* uses `,` as decimal separator and `;` as delimiter. Each graph has a **PNG** button (the graph with axes, cursors and a legend with the
    active cursor's values). *Tab as PDF* makes a landscape A4 with every graph, then the cursor table and the active band's statistics.
18. **Storage limit**: uploaded binaries and converted data are kept up to `VIEWER_STORAGE_LIMIT_GB` (20 by default; the sidebar shows the use). Beyond it the
    least recently used measurements are deleted; the ones open in a browser (heartbeat every minute) or used in the last 5 minutes never are.
    A measurement removed this way is reported when you restore a session ("upload it again").
19. **Formats**: numbers use the decimal separator of the browser language, times the browser's time zone (cursor table header, open-measurement start,
    PDF); the interface text stays English. Line colours come from a 12-colour palette tested for colour-vision deficiencies; when it runs out the same colours
    come back with a different dash.
20. **Refresh the page**: open measurements and their shifts, layout (including XY graphs and calculated channels), formulas, zoom, cursors, active tab and
    the accepted markers come back; the measurements are reopened by id from the Parquet cache, without converting them again. Closing the browser
    tab forgets everything; *Reset session* does it explicitly.

## Tests and benchmarks

```bash
cd backend && ../.venv/Scripts/python -m pytest      # unit, contract (all adapters), end-to-end API
cd frontend && npm test && npm run build              # vitest + type-check + production build
.venv/Scripts/python backend/benchmarks/bench_ingest.py   # 50M ingestion + series latency -> docs/benchmarks.md
```

Regenerate the API types after changing the endpoints:

```bash
.venv/Scripts/python backend/scripts/export_openapi.py && cd frontend && npm run gen:api
```

Configuration is by environment variables only; every field of `backend/src/viewer/config.py` is `VIEWER_<NAME>`. The main ones:

| Variable | Default | Meaning |
| --- | --- | --- |
| `VIEWER_ADAPTER` | `local` | `local` or `databricks` |
| `VIEWER_SOURCE_DIR`, `VIEWER_STORAGE_DIR` | `data/source`, `data/storage` | fake/real `.bin` files; blobs and Parquet |
| `VIEWER_METADATA_DIR`, `VIEWER_KVT_DIR` | unset | local Delta tables of metadata and KVT (extra `delta`); measurements appear next to the files |
| `VIEWER_SOURCE_TZ` | `Europe/Rome` | time zone of extractor and KVT timestamps |
| `VIEWER_STORAGE_LIMIT_GB`, `VIEWER_MAX_UPLOAD_MB`, `VIEWER_UPLOAD_CHUNK_MB`, `VIEWER_PIN_TTL_S` | 20, 2048, 50, 300 | retention and upload (step 6) |
| `VIEWER_LOG_LEVEL`, `VIEWER_LOG_FORMAT` | `INFO`, `text` | `json` writes one JSON object per line (Databricks) |
| `VIEWER_DBX_HOST`, `VIEWER_DBX_WAREHOUSE_ID` / `VIEWER_DBX_HTTP_PATH`, `VIEWER_DBX_METADATA_TABLE`, `VIEWER_DBX_KVT_TABLE`, `VIEWER_DBX_VOLUME_PATH`, `VIEWER_DBX_TOKEN` | unset | Databricks adapters |
| `VIEWER_LOCAL_CACHE_DIR`, `VIEWER_PERMISSION_TTL_S` | `data/cache`, 30 | Databricks: local Parquet copies; reuse time of a granted permission check |

Logs (RI-44): every request gets an id (`X-Request-ID` in, or generated, and returned in the response) that appears on every log record of the request and of the ingestion process it starts,
with the user and, for ingestion failures, the file, channel and phase. An access record per request has method, route, status, milliseconds and bytes. Tokens are never logged.

## Choices made in step 1

| Choice | Why |
| --- | --- |
| DuckDB for the external sort and channel reads | RI-04/06: sort with memory limit and spill, row-group statistics, reads one channel without loading the file. |
| pyarrow `ParquetWriter` for the temporary file | Row-group-per-slice writes (RI-04). Channel column dictionary-encoded in the final file. |
| pandas `tz_localize(ambiguous='infer')`, per channel | Mandated by RI-09. psutil (runtime) checks that an ingestion process is still alive, since the backend keeps no state. |
| Grid by `np.searchsorted` instead of polars `join_asof` | Same result (last sample with t ≤ t_k) without another dependency; all integer µs. |
| uPlot in mode 2 | RI-22: every series has its own x array. Presented in seconds; alignment stays on the integer k. |
| Arrow IPC for series (JSON kept as alternative) | Measured in step 2 ([docs/benchmarks.md](docs/benchmarks.md)): 2.4× smaller, ~13× faster warm. JSON stays selectable by `Accept` for curl/tests; say if you want it removed. |
| LRU cache of grids (`VIEWER_CACHE_MB`, 512) | RI-14: key (file, channel, n, mode, phase=0); full-range grid cached, windows sliced from it. |
| Axes drawn on a canvas layer, uPlot without y axes | RF-12/13: ticks only inside the band, lanes, aligned margins; the y scale of each axis is fixed by the band formula (RI-28), not by zoom. |
| TypeScript 5.9 (not 7) | `openapi-typescript` 7 does not run on TypeScript 7 yet. |
| Okabe-Ito colours for series | Colour-blind-safe default (RF-40 anticipated only as a constant; palette management is step 6). |

### Deviations and assumptions to confirm

- **`MeasurementSource.read(id)` returns the whole measurement** (frame + metadata), not blocks. Per-channel
  `ambiguous='infer'` needs a channel's samples in one piece, and RI-04 already accepts the whole-DataFrame memory peak. For the
  Delta KVT (step 7) the adapter can read in blocks internally and return the assembled frame.
- **Channel list before the conversion ends**: the extractor metadata (`signals`) is saved right after extraction, so
  names appear while converting (RI-05); the numeric catalogue arrives when the conversion is done.
- **`datetime` metadata is ignored for t0**; t0 is the first DataFrame timestamp (RI-05). The mock deliberately sets it earlier.
- **Event flag** is not in the metadata list, so it is detected: a channel is "event" if fewer than 60 % of its Δt are
  within ±30 % of the median Δt (`VIEWER_EVENT_REGULAR_FRACTION`).
- **Time-zone edge cases**: ambiguous hour that cannot be inferred and non-existent local times (spring forward) both abort the
  conversion with an explicit error naming file and channel. Identical consecutive stamps are collapsed for the inference and
  expanded afterwards (pandas cannot infer through repeated stamps); this does not affect the result.
- **Series request** accepts optional `j_start` / `j_end`. The full channel is still read per request (no cache in step 1).
- **Gap rule detail**: a grid point that coincides with a native sample keeps its value even at the edge of a gap; points
  after the last sample of a non-event channel are null.
- **Measurement ids** are the SHA-256 (first 32 hex chars) of the file content. Listing hashes files once and caches by
  (path, size, mtime) in memory.
- **Series API changed**: `channels` is now a list of `{name, mode}` (default mode: step for events, linear otherwise); average on an event channel is a 422.
- **Ticks sit on the divisions** of the band (min/max at its ends), so grid lines and all axes of a graph line up; automatic ranges are rounded outward to 1/2/2.5/5 × 10^k steps.
- **Number formatting** uses `.` as decimal separator; browser-locale formats belong to step 6 (RF-38).
- Layout, selection and zoom live in memory (lost on refresh): sessionStorage is step 3, JSON templates step 5. The marker-limit acceptance is per page session.
- Drag from the channel list onto an axis lane assigns to that axis; there is no axis-specific entry in the `+` menu (use *Properties → Axis*).
- **Cursor value rule**: last grid point at or before the cursor (`j = floor(k/n)`); no interpolation on the client (RI-24). After the end of a channel or in a hole the cell shows `—`.
- **Statistics** use the grid points inside the band (`j·n` between its edges); start/end are cursor readings at the two edges; Δ = end − start.
- **Session**: the layout is also stored in sessionStorage (decision for step 3) although the spec lists only measurements, shifts, cursors, zoom, tab and marker acceptance; it is never written to a file or the backend (templates are step 5). A snapshot with an unknown schema or invalid content is ignored. `shift` is reserved for step 4.
- **Shortcuts differ slightly from a literal reading of RF-39**: cursor cycling uses `[` `]` (not `Tab`, which would break keyboard focus navigation); `Alt+digit` may be captured by some browsers/OS.
- Keyboard shortcuts are ignored while typing in a form field.
- **"t0 on cursor"** (decision for step 4): it moves the *selected non-primary* measurement so that its start sits at the active cursor. The primary always stays at 0.
- **Shifts are whole 0.1 s steps** (RF-03); a shift that is not a multiple of a channel's step makes its grid miss the other's, which only matters for XY ("no common times") and, in step 5, for operators.
- **XY** uses the grid values of each channel in its chosen mode, at the coincident grid times; it always shows the whole measurement, not the visible time window; it does not take part in the margin alignment of the time graphs.
- **Closing the primary** promotes the next measurement and re-bases the others; cursors and zoom are moved by the same amount.
- The measurement selector (step 7) has VIN, configuration and start-date inputs, applied by the catalogue (SQL `WHERE` on Databricks); the text box above the list only narrows the shown names. Files from the source folder or uploads have no VIN/configuration/start, so any selector input hides them.
- Session `schema` is now 3 (formulas, aliases); snapshots from earlier steps are ignored.
- **Operator API**: `POST /operators/{name}` takes `inputs` (`ch` / `handle` / `const`), `params`, the `shifts` of the open measurements and `detail` (`summary` | `full`), and returns the handle with statistics; `full` adds the series (Arrow or JSON like `/series`). An unknown handle is a 404 with `code: "unknown_handle"`; the client then recomputes the chain once (RI-20). The handle includes operator, version, inputs, parameters, modes and the shifts of the files involved.
- **Formula orchestration is in the browser** (RI-23): topological levels, independent branches in parallel. `core/formula.py` is a small `graphlib` evaluator used only by the permanent equivalence tests of promoted operators (RI-21: `tests/test_operators.py`), and is the seed of a future batch executor.
- **Promoting a formula** (RF-33) = register it with `@operator(..., promoted_from=<formula doc>)`; the parametrised test then compares the operator with its source formula on reference data.
- **Template refs** are `slot label + channel name` (decision for step 5); a template written for A+B cannot be applied with only A open without replacing the B channels. Operator versions are stored; a version change or a deprecated/unknown operator produces a warning, not a failure.
- Calculated-channel results are cached by handle (bounded LRU, `VIEWER_RESULT_MB`); they are recomputed when a formula, a shift or the set of open measurements changes.
- Intermediate block previews use a server-side summary (200-bin sparkline); only the output block transfers the full series.
- **Upload protocol**: `POST /uploads` (filename, size, client fingerprint) → `PUT /uploads/{id}/chunks/{n}` (raw block, optional `X-Chunk-SHA256`) → `POST /uploads/{id}/complete` (content hash = measurement id). The upload id derives from the fingerprint, so the same file resumes; partial state lives in `BlobStorage` (survives a backend restart). `DELETE /uploads/{id}` cancels. The blob interface gained `size`, `move` and `compose` (concatenate parts, return the SHA-256).
- **Export** (`POST /export`) is computed by the backend from the cached grids (calculated channels by result handle); the CSV/Parquet files have exactly three columns, as decided; `time_s` is exact (step ÷ 10), and absolute times appear in the UI and the PDF only.
- **Retention** records the last use per measurement (`usage/<id>.json`, throttled) and the browser heartbeat (`POST /sessions/pin`) refreshes it; the protection window is `VIEWER_PIN_TTL_S` (300). It runs at start-up, after an upload and after a conversion. If the newest data alone exceeds the limit nothing in use is removed (the limit is then exceeded until it is idle). Source-folder binaries are never deleted, only their converted data. `GET /storage` reports the use.
- **jsPDF** (about 130 kB gzip, loaded on first use) draws the PDF: the standard PDF fonts have no Greek or symbol glyphs, so Δ, ƒ, ⚠ appear as `Delta`, `f`, `!` there. Graphs are embedded as images.
- **Palette**: colours and thresholds are in `palette.test.ts` (smallest difference between any two colours: ΔE ≥ 25 normal vision, ≥ 14 for protan/deutan/tritan; the first six ≥ 16). The two lightest colours (`#DDCC77`, `#BBBBBB`) are weak on a white background: they are the 9th and 12th.
- **Settings** (environment): `VIEWER_MAX_UPLOAD_MB` (2048), `VIEWER_UPLOAD_CHUNK_MB` (50), `VIEWER_STORAGE_LIMIT_GB` (20, 0 = off), `VIEWER_PIN_TTL_S` (300).
- A latent bug fixed on the way: the duration of an open measurement was written to the raw object instead of the reactive list, so the shared axis could stay at 0 when a measurement was opened right after an upload.

## Choices made in step 7

| Choice | Why |
| --- | --- |
| Databricks adapters built and tested against fakes | No workspace was reachable. `FakeWarehouse` runs the **real SQL strings** (shared with the local Delta adapter in `core/kvt.py`) on DuckDB with per-token grants; `LocalFiles` stands in for the Volume. The same contract tests run on memory, local, local-Delta and Databricks adapters. |
| Local Delta adapter (`deltalake`) | Lets the whole KVT path (selector, metadata t0/end, DST rule, ingestion) run and be benchmarked on a PC. Extra `delta`. |
| `databricks-sql-connector` + `databricks-sdk` only inside `adapters/databricks/clients.py` | RI-33: swapping the platform touches one file. Extra `databricks`. |
| Request-scoped services | With a user token the catalogue and source are bound to it; caches and stores stay shared. `CheckedCatalog.get` is the permission check before anything cached is served. |
| `hatch-vcs` for the version | The tag is the single source of truth; `release_check.py` refuses malformed or non-increasing tags. |
| Frontend inside the wheel | The static folder is built by `scripts/build_frontend.py` (not committed) and force-included by hatch; a test opens the wheel. |

### Deviations and assumptions to confirm (step 7)

- **t0 and end of a KVT measurement** come from `startOfSampling` / `endOfSampling` (RI-05). If the first sample is earlier than the metadata start, a warning is shown and the first sample is used (so no sample lies before t0); binaries keep the first timestamp.
- **Measurement id of a KVT measurement is its uuid**, sorted most recent start first. The list is capped at **500** (`GET /files?limit=`, max 5000) because each row needs a status lookup (20,000 rows took 12 s); the UI says when the list is capped.
- **KVT values**: numeric, boolean (`true/false`) or numeric strings; other strings exclude the channel with a warning that names it (the same rule as the binaries). Rows are read in acquisition order, which the fall-back-hour inference needs.
- **Uploads are shared** between all users of the app (decision); KVT measurements follow Unity Catalog permissions. Handles created by one user are unusable by another.
- **Permission check is cached 30 s** (`VIEWER_PERMISSION_TTL_S`): a revoked right takes up to that long to act on the shared cache; the metadata/KVT are always read with the user's token.
- **Without a forwarded token the app answers 401** when `VIEWER_ADAPTER=databricks` (unless `VIEWER_DBX_TOKEN` is set for a local run).
- `GET /api/v1/version` and `GET /api/v1/me` (user, adapter, version) feed the sidebar footer.
- **Not verified on a real workspace**: on-behalf-of token, Volume access with the app identity, warehouse latency for 50M rows, KVT partitioning, pipeline YAML execution on Azure DevOps (it is parsed and cross-checked by tests, not run). Full list: [docs/databricks.md](docs/databricks.md#not-verified--check-these-first).
