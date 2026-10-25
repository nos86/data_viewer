"""Runtime configuration, read from environment variables only (RI-03)."""
from __future__ import annotations

import os
from dataclasses import dataclass, fields
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    adapter: str = "local"
    source_dir: Path = Path("data/source")      # folder holding the .bin measurements
    storage_dir: Path = Path("data/storage")    # blobs (status, metadata) and Parquet
    metadata_dir: Path | None = None            # Delta table with the measurement metadata (uuid, path, vin, configurationId, startOfSampling, endOfSampling)
    kvt_dir: Path | None = None                 # Delta table (uuid, key, value, timestamp), best partitioned by uuid
    # Databricks (adapter = databricks)
    dbx_host: str = ""                          # https://adb-....azuredatabricks.net
    dbx_http_path: str = ""                     # SQL warehouse HTTP path (/sql/1.0/warehouses/<id>) ...
    dbx_warehouse_id: str = ""                  # ... or just the warehouse id, from which the path is derived
    dbx_metadata_table: str = ""                # catalog.schema.table
    dbx_kvt_table: str = ""
    dbx_volume_path: str = ""                    # /Volumes/catalog/schema/volume/folder: uploads, status, Parquet
    dbx_token: str = ""                          # fallback when no user token is forwarded (local runs); never logged
    local_cache_dir: Path = Path("data/cache")   # on-disk copy of Parquet files used by DuckDB
    permission_ttl_s: int = 30                   # a granted permission check is reused this long
    source_tz: str = "Europe/Rome"              # time zone of extractor/KVT timestamps
    slice_rows: int = 1_000_000                 # rows per Parquet row group in the temp file
    max_gap_factor: float = 1.5                 # RI-12
    duckdb_memory_limit: str = "2GB"
    result_mb: int = 256                        # operator results kept for handles (RI-20)
    max_upload_mb: int = 2048                   # largest upload accepted (RF-37)
    upload_chunk_mb: float = 50.0                 # size of one upload block (RI-05)
    storage_limit_gb: float = 20.0                # uploaded binaries + Parquet cache; 0 = no limit (RI-39)
    pin_ttl_s: int = 300                        # a measurement used this recently is never evicted
    log_level: str = "INFO"
    log_format: str = "text"                    # 'json' for log collectors (Databricks)
    cache_mb: int = 512                         # LRU cache of computed grids (RI-14)
    event_regular_fraction: float = 0.6         # below this share of regular Δt a channel is "event"

    @classmethod
    def from_env(cls, **overrides) -> "Settings":
        kwargs = {}
        for f in fields(cls):
            raw = os.environ.get("VIEWER_" + f.name.upper())
            if raw is None:
                continue
            kwargs[f.name] = Path(raw) if "Path" in str(f.type) else type(getattr(cls, f.name))(raw)
        kwargs.update(overrides)
        return cls(**kwargs)

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}
