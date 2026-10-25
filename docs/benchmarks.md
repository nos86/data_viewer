# Benchmarks — step 1

Reproduce: `python backend/benchmarks/bench_ingest.py` (generates `data/source/mock_large_50M.bin` if missing;
raw result in `data/bench/result.json`).

**Machine:** Windows 11, 20 logical CPUs, 31.7 GiB RAM, local SSD. Python 3.12, pandas 3.1, pyarrow 26, DuckDB 2.0.
**Dataset:** mock `large` profile — 420 channels (30 × 100 Hz, 120 × 10 Hz, 40 × 5 Hz, 50 × 2 Hz, 100 × 1 Hz,
50 × 3 Hz, 30 event), 3 h, **51,027,883 samples** after de-duplication, float64 only (decision for this benchmark: a
mixed float/bool/str `value` column becomes `object` dtype and would make the 50M DataFrame several times larger;
the bool/str paths are covered by the test suite on small data). Jitter, isolated missing samples, long interruptions
and duplicated timestamps are present. Output Parquet: 492 MiB.

## Ingestion (separate process, single run)

| Phase | Time |
| --- | --- |
| Extraction (the **mock generator**, stand-in for the real extractor) | 6.7 s |
| Conversion: categorical, per-channel tz→UTC, de-dup, slice writes (1M rows/row group) | 18.3 s |
| DuckDB sort `ORDER BY channel, t` (2 GB limit, spill enabled) + write | 2.7 s |
| Catalogue | < 0.01 s |
| **Total** | **27.8 s** (wall 28.7 s) |
| **Total without extraction** | **21.0 s** |

Peak resident memory of the ingestion process: **3.34 GiB** (during extraction, i.e. the DataFrame being built);
2.99 GiB in the conversion phase, 2.33 GiB while sorting (DataFrame already released).

**Against the target (≤ 1 min):** met, with ~2× margin. The real extractor time is not included and will add to it.
The peak memory is dominated by the DataFrame itself, as RI-04 states; what the wrapper adds on top is small.

Caveats: one run on one machine; the mock extractor is much faster than a real binary parser; the `value` column is
float64 (an `object` column would raise both memory and conversion time).

## `POST /api/v1/files/{id}/series` latency (step mode, JSON by columns, via TestClient in-process)

Seven requests per row; "first" is the first request, "repeat" the median of the other six. There is no application
cache in step 1, so repeat measures a warm OS/file cache, not an LRU hit. The Parquet file had just been written, so
the "first" request is not a cold-disk figure.

| Channel | Points | JSON size | First | Repeat (median) |
| --- | --- | --- | --- | --- |
| `f10_000` (10 Hz native, n = 1) | 108,001 | 1.89 MiB | 78 ms | 43 ms |
| `f100_000` (100 Hz native → 10 Hz grid) | 108,001 | 2.06 MiB | 66 ms | 64 ms |
| `f1_000` (1 Hz, n = 10) | 10,801 | 0.21 MiB | 32 ms | 31 ms |
| the three together | 226,803 | 4.16 MiB | 124 ms | 122 ms |

**Against the target (fast channel, first access ≤ 2 s):** met (≈ 0.07–0.08 s server side). Network transfer and
browser JSON parsing are not included; in the browser the whole select → draw sequence feels immediate.

## Not measured in this step

KVT read time (step 7). Transfer format, rendering, marker threshold, cached latency and zoom response were measured in step 2 (below).

---

# Benchmarks — step 2

Same machine and 51M-sample dataset as above (ingested through the app: 25.8 s). Measured in the Browser pane of the desktop app
(Chromium) against the dev servers; requests go through the Vite proxy on localhost. Single runs/medians of 5–6 repeats; indicative, not a lab measurement.

## Transfer format: Arrow IPC vs JSON by columns (RI-15)

`POST /series`, 108,001 points per channel (3 h at 10 Hz), mode `linear`. "Server cache" = the grid is already in the LRU cache.
Client time = fetch (includes server compute/encode) + decode into `Float64Array`.

| Request | Format | Payload | Cold (grid computed) | Warm (server cache) | Client decode |
| --- | --- | --- | --- | --- | --- |
| 1 channel | JSON | 2.0 MiB | 75 ms | 42 ms | 3–4 ms |
| 1 channel | **Arrow** | **0.84 MiB** | **39 ms** | **8 ms** | **0.1–0.2 ms** |
| 10 channels (1.08M points) | JSON | 20 MiB | 698 ms | 372–412 ms | 29–33 ms |
| 10 channels (1.08M points) | **Arrow** | **8.4 MiB** | **322 ms** | **29 ms** | **0.3–0.4 ms** |

**Decision: Arrow IPC** is the transfer format used by the frontend (≈ 2.4× smaller, ≈ 13× faster warm, decode is free because the
float64 buffer is used directly). Cost: the `apache-arrow` npm dependency (the whole frontend bundle grew from 51 to 99 kB gzip, Arrow and the new UI together) and an
untyped response in OpenAPI (metadata travels in the `X-Series-Meta` header). The JSON representation is kept as the documented,
typed alternative for non-browser clients and tests (`Accept` header chooses); it can be removed if you prefer a single format.

## Latency against the targets

| Scenario | Target | Measured |
| --- | --- | --- |
| Fast channel, first access | ≤ 2 s | 39 ms (1 channel), 322 ms (10 channels at once) |
| Channel already in cache | ≤ 0.2 s | 8 ms (1 channel), 29 ms (10 channels) |

## Rendering and zoom: 4 graphs × 10 channels × 108,001 points (4.3M points)

| Operation | Time |
| --- | --- |
| Zoom/pan step (wheel) in one graph, all four repaint (synchronised x), script + canvas flush | 70–350 ms (median ≈ 100 ms; larger when zoomed out) |
| Rebuild of one graph (10 × 108k points) after a style change | 25–95 ms |

**Against the target (zoom/pan ≤ 500 ms): met** on this machine. The figures are the time until a `setTimeout(0)` scheduled right after the
event fires, i.e. all synchronous scripting and canvas commands are done; GPU rasterisation and compositing are not included.

## Markers

uPlot draws 10 series × 50,000 markers in 40 ms (10 × 10,000: 14 ms; 1 × 50,000: 5 ms) on a 900 × 200 canvas, almost the same as the
lines alone, so rendering cost is not the limiting factor; legibility is (above a few hundred points per plot width the markers merge).
**Threshold chosen: 10,000 visible points per series** — above it the user is asked to accept (session) or turn markers off (RF-16).

---

# Benchmarks — step 3

No backend change. Same setup as step 2 (Browser pane, 51M-sample measurement, 4 graphs × 10 channels × 108,001 points, one cursor).

| Operation | Result |
| --- | --- |
| Move the active cursor with the keyboard (legend values of 40 lines, cursor table and 4 cursor layers updated), until the next frame | median 66 ms, p90 68 ms, max 101 ms (30 moves) |
| uPlot instances rebuilt while moving cursors | none (the same `.u-over` elements before and after: cursors are a separate canvas, RI-29) |

The figure includes waiting for the next animation frame of the pane, so the work itself is lower. Band statistics are recomputed
only for the active band and the lines of the current tab (a few hundred grid points to 108k per line, one pass each).

---

# Benchmarks — step 4

No backend change. Browser pane, mock 51M-sample measurement, XY graph with X = `f10_000` and 4 Y lines (`f10_001`…`f10_004`), 108,001 points each (432k points), one cursor.

| Operation | Result |
| --- | --- |
| `xyPoints` (common times of two 108k-point grids, index arithmetic) | 2–4 ms |
| Redraw of the XY graph (4 lines × 108k points, polylines) after a cursor move, until the next frame with canvas read-back | 18–20 ms (frame-limited) |

Opening a second measurement adds only its own series requests; changing a shift never refetches (the shift is applied to the x coordinates in the browser).

---

# Benchmarks — step 5

Operators on the 51M-sample measurement, 10 Hz channels (108,001 points), browser → Vite proxy → API, single runs, indicative.

| Call | Time |
| --- | --- |
| `sub(f10_100, f10_101)`, `detail: summary`, nothing cached (reads two channels, builds both grids, stats + sparkline) | 74 ms |
| the same call again | 11 ms (grids come from the series cache) |
| `moving_average(handle)`, summary | 10 ms |
| `moving_average(handle)`, **full**, Arrow / JSON | 13 ms / 60 ms |
| 4-block formula (`neg` ‖ `abs` → `add` → `derivative`, output full), cold | 69 ms |

Intermediate blocks only return statistics and a 200-bin sparkline (a few kB); only the output block sends the 108k-point series. Handles are
kept server-side in an LRU of results (`VIEWER_RESULT_MB`, 256 by default); if one is gone the client recomputes the chain once.

---

# Benchmarks — step 6

Same machine as before. `python backend/benchmarks/bench_step6.py` (in-process through the API: network not included).

## Upload (RI-05): 500 MB in 50 MB blocks

| Phase | Time |
| --- | --- |
| 10 blocks received and stored (with per-block SHA-256 check) | 0.67 s |
| SHA-256 of the blocks on the client side | 0.21 s |
| Assembly (`compose`) of the 500 MB file with its content hash | 0.69 s |
| **Total** | **1.95 s (≈ 256 MB/s)** |

Over a real network the transfer time dominates. In the browser, hashing a block uses `crypto.subtle` (hardware accelerated) and a block is
held in memory once (50 MB). The partial blocks stay on the server: an interrupted upload resumes with only the missing blocks.

## Export (RF-35): 4 graphs × 10 channels × 108,001 points (4.29M rows) of the 51M-sample measurement

| Format | Cold (grids not cached) first byte / total | Warm first byte / total | Size |
| --- | --- | --- | --- |
| CSV (streamed in pieces of 500k rows) | 1.45 s / 2.51 s | 0.11 s / 1.15 s | 150 MiB |
| Parquet (zstd) | 0.98 s / 1.17 s | 0.95 s / 1.12 s | 52 MiB |

≈ 3.7M rows/s for CSV, ≈ 3.8M rows/s for Parquet once the grids are in the cache.

## Browser (indicative)

Tab PDF with one graph, one cursor table: 345 ms (the first export also loads jsPDF, about 130 kB gzip, on demand). PNG of a graph: below 100 ms.

---

# Benchmarks — step 7

Same machine (20 CPUs, 31.7 GiB). `python backend/benchmarks/bench_step7.py` (ingestion in a separate process as in the app; the API through `TestClient`, so the network is not included).
**No Databricks workspace was reachable**: the warehouse path is not measured; the local Delta KVT has the same layout (partitioned by `uuid`, columns `uuid, key, value, timestamp`) and goes through the same ingestion code.

## 50M-sample measurement: local Delta KVT vs binary path (target ≤ 1 min)

51,047,159 rows, 420 channels, 3 h. The Delta KVT is 695 MiB (Parquet, written in 5M-row files); writing it took 17.5 s.

| Phase | Delta KVT | Binary (mock extractor) |
| --- | --- | --- |
| extracting (read the source) | 1.7 s | 6.2 s (the mock generator, stand-in for the real extractor) |
| converting (local → UTC, grid keys, Parquet) | 17.3 s | 20.6 s |
| sorting (DuckDB, external) | 3.3 s | 3.2 s |
| **total** | **22.4 s** (wall 23.5 s) | **30.0 s** (wall 31.2 s) |
| peak memory of the ingestion process | 3.2 GiB | 3.4 GiB |

The KVT path meets the target with a wide margin and costs the same as the binary path once extraction is left out (≈ 20 s of converting + sorting on both). On Databricks the extraction
phase becomes the SQL fetch of 51M rows; its time depends on the warehouse and network and is the first thing to measure there (`scripts/databricks_smoke.py --adapters` prints it for one measurement).

## Selector (GET /files) over 20,000 metadata rows (local Delta, 400 VINs, 12 configurations)

| Request | Rows returned | Median |
| --- | --- | --- |
| no selector (capped at 500, most recent first) | 500 | 565 ms |
| `vin=` | 50 | 158 ms |
| `vin=` + `configuration_id=` + date range | 8 | 134 ms |

About 130 ms of each call is reading and filtering the metadata Delta table (a warehouse does this in SQL, with the same `WHERE`). The rest is two blob reads per returned row (status and metadata files). Before the
cap, the unfiltered list of 20,000 rows took 12.5 s: that is why `GET /files` returns at most 500 rows (`limit`, up to 5000) and the UI asks to narrow the selection.

## Request logging

Round trip of `GET /api/v1/version` through `TestClient` (middleware, request id, access record formatted and written to a null stream), 3,000 calls:

| Logging | µs per request |
| --- | --- |
| level WARNING (no access record) | 1585 |
| INFO, text | 1594 |
| INFO, JSON | 1500 |

The difference between them (< 10 µs) is below the measurement noise: logging is not a cost at the rates of this app.
