# Running on Databricks

The same package runs locally and as a Databricks App; only configuration changes (`VIEWER_ADAPTER=databricks`, RI-34).
**Nothing in this document was verified against a real workspace**: none was reachable while the step was built. The adapters are tested against
fakes that run the real SQL on DuckDB and keep files in a folder (`backend/tests/fakes.py`); the list at the end says what to check first.

## What runs where

| Piece | Databricks | Local |
| --- | --- | --- |
| Measurement list and metadata (RF-01, RI-05) | `DbxCatalog`: SQL on a **SQL warehouse**, table `VIEWER_DBX_METADATA_TABLE` (`uuid, path, vin, configurationId, startOfSampling, endOfSampling`) | Delta folder `VIEWER_METADATA_DIR` |
| Samples | `DbxSource`: `SELECT key, value, timestamp FROM <KVT> WHERE uuid = ?` | Delta folder `VIEWER_KVT_DIR` |
| Uploaded binaries, status, metadata, usage records | `VolumeBlob` on a **Unity Catalog Volume** (`VIEWER_DBX_VOLUME_PATH/blobs/…`) | `VIEWER_STORAGE_DIR` |
| Converted data (Parquet) | `VolumeSeriesStore`: file on the Volume, a copy on the app's local disk (`VIEWER_LOCAL_CACHE_DIR`) that DuckDB reads | `VIEWER_STORAGE_DIR/parquet` |
| Identity | headers `X-Forwarded-Access-Token`, `X-Forwarded-Email` set by the Databricks Apps proxy | none |

Only `adapters/databricks/clients.py` imports `databricks-sdk` / `databricks-sql-connector`.

## Permissions (RI-40)

* The catalogue and the KVT are read **with the token of the calling user** (on-behalf-of): Unity Catalog decides what the user lists and reads.
* Before anything from the shared cache (Parquet, grids, operator results) is served, the user's catalogue is asked for that measurement. No row → 404, whatever the cache holds.
  A granted check is remembered for `VIEWER_PERMISSION_TTL_S` (30 s): a revocation takes at most that long. `0` checks on every call.
* Operator handles remember who used them (`Result.users`): a handle created for user A is unknown to user B.
* **Uploaded binaries are visible to every user of the app** (decision, step 7): the Volume belongs to the app. Restrict who may open the app if that is not acceptable.
* The Volume is accessed with the **app's own identity** (service principal), not the user's.
* Tokens are never logged.

## Deploy

Prerequisites (once per workspace):

1. A Unity Catalog **Volume** for the app, e.g. `main.measurements.viewer`, and a folder in it (`/Volumes/main/measurements/viewer/app`). Grant the app's service principal `READ VOLUME` and `WRITE VOLUME`.
2. The two tables (metadata, KVT). Users need `SELECT` on both (directly or through groups). **Partition or liquid-cluster the KVT by `uuid`**: every read is `WHERE uuid = ?`.
3. A **SQL warehouse** the users may use (`CAN USE`). It is a bundle resource of the app.
4. Databricks Apps with **user authorization** (on-behalf-of) enabled for the workspace, and the scope `sql` requested for the app.
5. Azure DevOps: environments `viewer-staging` and `viewer-production` (put the approval check on production), a variable group `viewer-databricks` with
   `DATABRICKS_HOST_STAGING`, `DATABRICKS_HOST_PRODUCTION` and the secrets `DATABRICKS_TOKEN_STAGING`, `DATABRICKS_TOKEN_PRODUCTION`
   (a service principal's token; prefer a workload-identity service connection if your organisation has one). Fill the `<…>` values in `app.yaml` and `databricks.yml`.

Release: tag `vMAJOR.MINOR.PATCH` on `main`. `azure-pipelines.yml` then runs **Test** (pytest on Python 3.11 and 3.12, vitest, type check, build; the tag must be well formed and newer than every other),
**Build** (frontend → `viewer/static` → wheel and sdist whose version is the tag), **Staging** (`databricks bundle deploy -t staging`, `bundle run viewer_app`, smoke check that `/api/v1/version` answers the tag's version),
then **Production** after approval. Pushes to `main` and `dev` only test and build.

By hand:

```bash
python scripts/build_frontend.py && python -m build backend --outdir dist
cp dist/viewer-*.whl . && echo "./$(ls viewer-*.whl)[databricks]" > requirements.txt
databricks bundle deploy -t staging --var wheel=$(ls viewer-*.whl) --var version=1.0.0
databricks bundle run -t staging viewer_app
python scripts/databricks_smoke.py --app-url <app url> --expect-version 1.0.0
```

## Settings

| Variable | Meaning |
| --- | --- |
| `VIEWER_ADAPTER` | `databricks` (default `local`) |
| `VIEWER_DBX_HOST` | workspace URL |
| `VIEWER_DBX_WAREHOUSE_ID` or `VIEWER_DBX_HTTP_PATH` | SQL warehouse (the path is `/sql/1.0/warehouses/<id>`) |
| `VIEWER_DBX_METADATA_TABLE`, `VIEWER_DBX_KVT_TABLE` | `catalog.schema.table` |
| `VIEWER_DBX_VOLUME_PATH` | Volume folder for uploads, status and Parquet |
| `VIEWER_DBX_TOKEN` | only for a run outside Databricks (your own token); never set it in the app |
| `VIEWER_LOCAL_CACHE_DIR` | local disk copy of Parquet files (app disk is ephemeral: a restart re-downloads what is used) |
| `VIEWER_PERMISSION_TTL_S` | how long a granted permission check is reused (30) |
| `VIEWER_LOG_LEVEL`, `VIEWER_LOG_FORMAT` | `INFO` / `json` for Databricks (the log collector reads JSON lines on stdout) |

Running the app on a PC against the workspace (to try it before deploying): set the variables above plus `VIEWER_DBX_TOKEN`, then `viewer serve`.

## Checking a deployment

`python scripts/databricks_smoke.py --adapters` (with the settings above and `DATABRICKS_TOKEN`) lists what the token sees, reads one KVT measurement through the warehouse,
and round-trips a blob and a Parquet file on the Volume. `--app-url … --expect-version …` checks a deployed app.

## Not verified — check these first

1. **On-behalf-of token**: that the app receives `X-Forwarded-Access-Token`, that the connector accepts it as `access_token`, and that the scope allows SQL.
   If the header is missing the app answers 401 (no silent fallback to the app identity).
2. **Volume access**: `files.upload / download / get_metadata / list_directory_contents` with the app's service principal and the exact exceptions of the SDK version
   in use (`NotFound` is matched by class name). `move` is copy + delete (the Files API has no rename).
3. **Warehouse sizing and latency**: one KVT read is a single `SELECT … WHERE uuid = ?` fetched as Arrow. The time for 50M rows over the network, and
   whether the warehouse starts quickly enough (serverless recommended), is unmeasured. The local equivalent is in `docs/benchmarks.md` (step 7).
4. **KVT layout**: partitioning or clustering by `uuid`; value type (double, or string as in the mock `--value-type string`: strings are parsed to numbers/booleans, others reported and the channel excluded).
5. **Timestamps**: local naive time in `timestamp`, `startOfSampling`, `endOfSampling` (as the extractor); if the tables hold UTC, `VIEWER_SOURCE_TZ=UTC` is the only change needed.
6. **Disk**: the app's local disk must hold the Parquet files in use plus the DuckDB spill (`VIEWER_DUCKDB_MEMORY_LIMIT`).
7. **Several app instances**: they share the Volume (Parquet, status, usage) but not memory (grid cache, operator results): a handle created on one instance is recomputed by the client on another (`unknown_handle` → one recompute).
