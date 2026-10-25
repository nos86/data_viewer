"""Databricks adapters: tables on a SQL warehouse, files on a Unity Catalog Volume (RI-03, RI-33…36, RI-40).

Everything here depends only on the protocols of `clients.py` and on the shared definitions of core/kvt.py, so the same behaviour is
tested with fakes. Switch with VIEWER_ADAPTER=databricks (see docs/databricks.md); no other code changes (RI-34).
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

from viewer.adapters.databricks.clients import FilesClient, SqlClient
from viewer.adapters.routing import MergedCatalog, RoutingSource
from viewer.adapters.local import LocalSeriesStore
from viewer.core.kvt import Filters, MetaRow, info_of, meta_from_row, source_data, sql_get, sql_kvt, sql_list
from viewer.core.models import IngestError, MeasurementInfo, SourceData
from viewer.ports import BlobStorage

__all__ = ["DbxCatalog", "DbxSource", "VolumeBlob", "VolumeSeriesStore", "BlobUploadCatalog", "BlobFileSource", "build_databricks"]


class DbxCatalog:
    """MeasurementCatalog over the metadata table, read with the credentials the SqlClient was built with (the user's)."""

    def __init__(self, sql: SqlClient, table: str):
        self._sql, self._table = sql, table

    def _rows(self, query: str, params: list[Any]) -> list[MetaRow]:
        return [meta_from_row(r) for r in self._sql.query(query, params).to_pylist()]

    def list(self, filters: Filters | None = None) -> list[MeasurementInfo]:
        q, p = sql_list(self._table, filters)
        return [info_of(m) for m in self._rows(q, p)]

    def row(self, measurement_id: str) -> MetaRow | None:
        rows = self._rows(sql_get(self._table), [measurement_id])
        return rows[0] if rows else None

    def get(self, measurement_id: str) -> MeasurementInfo | None:
        m = self.row(measurement_id)
        return info_of(m) if m else None


class DbxSource:
    """The KVT of one measurement, read from the warehouse (Unity Catalog decides whether the user may)."""

    def __init__(self, sql: SqlClient, catalog: DbxCatalog, kvt_table: str):
        self._sql, self._catalog, self._kvt = sql, catalog, kvt_table

    def has(self, measurement_id: str) -> bool:
        return self._catalog.row(measurement_id) is not None

    def read(self, measurement_id: str) -> SourceData:
        m = self._catalog.row(measurement_id)
        if m is None:
            raise KeyError(measurement_id)
        table = self._sql.query(sql_kvt(self._kvt), [measurement_id])
        if table.num_rows == 0:
            raise IngestError(f"{m.path or measurement_id}: the KVT has no rows for this measurement")
        return source_data(m, table)


class VolumeBlob:
    """BlobStorage on a Volume: key 'a/b.json' is the file <root>/blobs/a/b.json."""

    def __init__(self, files: FilesClient, root: str, scratch: Path):
        self._f, self._root, self._scratch = files, root.rstrip("/") + "/blobs", Path(scratch)
        self._scratch.mkdir(parents=True, exist_ok=True)

    def _p(self, key: str) -> str:
        if ".." in key.split("/") or key.startswith("/"):
            raise ValueError(f"bad key {key!r}")
        return f"{self._root}/{key}"

    def write(self, key: str, data: bytes) -> None:
        self._f.upload_bytes(self._p(key), data)

    def read(self, key: str) -> bytes:
        if not self._f.exists(self._p(key)):
            raise KeyError(key)
        return self._f.download_bytes(self._p(key))

    def exists(self, key: str) -> bool:
        return self._f.exists(self._p(key))

    def list(self, prefix: str = "") -> list[str]:
        base = self._root + "/"
        keys = [p[len(base):] for p, _ in self._f.list_files(self._root)]
        return sorted(k for k in keys if k.startswith(prefix))

    def delete(self, key: str) -> None:
        self._f.delete(self._p(key))

    def size(self, key: str) -> int:
        if not self._f.exists(self._p(key)):
            raise KeyError(key)
        return self._f.size(self._p(key))

    def move(self, src_key: str, dst_key: str) -> None:
        """The Files API has no rename: copy through a scratch file, then delete (one large binary per upload, not per read)."""
        if not self._f.exists(self._p(src_key)):
            raise KeyError(src_key)
        tmp = self._scratch / f"move-{hashlib.sha1(src_key.encode()).hexdigest()}"
        self._f.download_file(self._p(src_key), tmp)
        try:
            self._f.upload_file(self._p(dst_key), tmp)
        finally:
            tmp.unlink(missing_ok=True)
        self._f.delete(self._p(src_key))

    def compose(self, dest_key: str, part_keys: list[str]) -> str:
        """Concatenates the parts into one file (on local scratch disk, hashing as it goes), then uploads it."""
        tmp = self._scratch / f"compose-{hashlib.sha1(dest_key.encode()).hexdigest()}"
        h = hashlib.sha256()
        try:
            with open(tmp, "wb") as out:
                for k in part_keys:
                    if not self._f.exists(self._p(k)):
                        raise KeyError(k)
                    block = self._f.download_bytes(self._p(k))
                    h.update(block)
                    out.write(block)
            self._f.upload_file(self._p(dest_key), tmp)
        finally:
            tmp.unlink(missing_ok=True)
        return h.hexdigest()


class VolumeSeriesStore:
    """Parquet series: sorted and queried by DuckDB on a local disk copy, persisted on the Volume (shared between app instances).

    Reads fetch the file from the Volume when the local copy is missing (a restarted or second app instance)."""

    def __init__(self, files: FilesClient, root: str, local: LocalSeriesStore):
        self._f, self._root, self._local = files, root.rstrip("/") + "/parquet", local

    def _remote(self, mid: str) -> str:
        return f"{self._root}/{mid}.parquet"

    def begin_write(self, measurement_id: str):
        inner = self._local.begin_write(measurement_id)
        store = self

        class _Writer:
            def write_block(self, block):
                inner.write_block(block)

            def commit(self):
                inner.commit()
                store._f.upload_file(store._remote(measurement_id), store._local._path(measurement_id))

            def abort(self):
                inner.abort()

        return _Writer()

    def _ensure(self, mid: str) -> bool:
        if self._local.exists(mid):
            return True
        if self._f.exists(self._remote(mid)):
            self._f.download_file(self._remote(mid), self._local._path(mid))
            return True
        return False

    def exists(self, measurement_id: str) -> bool:
        return self._local.exists(measurement_id) or self._f.exists(self._remote(measurement_id))

    def channels(self, measurement_id: str) -> list[str]:
        return self._local.channels(measurement_id) if self._ensure(measurement_id) else []

    def read_channel(self, measurement_id, channel, t_from_us=None, t_to_us=None):
        self._ensure(measurement_id)
        return self._local.read_channel(measurement_id, channel, t_from_us, t_to_us)

    def delete(self, measurement_id: str) -> None:
        self._local.delete(measurement_id)
        self._f.delete(self._remote(measurement_id))

    def size(self, measurement_id: str) -> int:
        return self._f.size(self._remote(measurement_id)) if self._f.exists(self._remote(measurement_id)) else 0

    def ids(self) -> list[str]:
        return sorted(p.rsplit("/", 1)[-1][: -len(".parquet")] for p, _ in self._f.list_files(self._root) if p.endswith(".parquet"))


class BlobUploadCatalog:
    """Uploaded binaries (uploads/<id>.bin + sidecar) as measurements, on any BlobStorage."""

    def __init__(self, blobs: BlobStorage):
        self._b = blobs

    def _ids(self) -> list[str]:
        return sorted(k[len("uploads/"): -len(".bin")] for k in self._b.list("uploads/") if k.endswith(".bin") and k.count("/") == 1)

    def _info(self, mid: str) -> MeasurementInfo:
        name = f"{mid}.bin"
        try:
            name = json.loads(self._b.read(f"uploads/{mid}.json")).get("name", name)
        except (KeyError, ValueError):
            pass
        return MeasurementInfo(id=mid, name=name, path=f"uploads/{mid}.bin", size_bytes=self._b.size(f"uploads/{mid}.bin"))

    def list(self, filters: Filters | None = None) -> list[MeasurementInfo]:
        return [] if filters is not None and filters.any() else [self._info(i) for i in self._ids()]

    def get(self, measurement_id: str) -> MeasurementInfo | None:
        return self._info(measurement_id) if self._b.exists(f"uploads/{measurement_id}.bin") else None


class BlobFileSource:
    """Runs the extractor on an uploaded binary (copied to a scratch file, the extractor wants a path)."""

    def __init__(self, blobs: BlobStorage, catalog: BlobUploadCatalog, scratch: Path):
        self._b, self._c, self._scratch = blobs, catalog, Path(scratch)

    def read(self, measurement_id: str) -> SourceData:
        info = self._c.get(measurement_id)
        if info is None:
            raise KeyError(measurement_id)
        from viewer import extractor_placeholder

        self._scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=self._scratch, suffix=".bin", delete=False) as f:
            f.write(self._b.read(f"uploads/{measurement_id}.bin"))
            path = f.name
        try:
            frame, meta = extractor_placeholder.extract(path)
        except Exception as e:
            raise IngestError(f"{info.name}: extractor failed: {e}") from e
        finally:
            Path(path).unlink(missing_ok=True)
        return SourceData(frame=frame, meta=meta)


def build_databricks(settings, sql_factory=None, files: FilesClient | None = None) -> dict:
    """The four adapters (+ the per-user factory). `sql_factory(token) -> SqlClient` and `files` are injectable for tests."""
    from viewer.adapters.databricks.clients import ConnectorSqlClient, SdkFilesClient

    http_path = settings.dbx_http_path or (f"/sql/1.0/warehouses/{settings.dbx_warehouse_id}" if settings.dbx_warehouse_id else "")
    missing = [("VIEWER_" + n.upper()) for n in ("dbx_host", "dbx_metadata_table", "dbx_kvt_table", "dbx_volume_path") if not getattr(settings, n)]
    if not http_path:
        missing.insert(1, "VIEWER_DBX_HTTP_PATH (or VIEWER_DBX_WAREHOUSE_ID)")
    if missing and (sql_factory is None or files is None):
        raise ValueError("VIEWER_ADAPTER=databricks needs " + ", ".join(missing))
    files = files or SdkFilesClient(settings.dbx_host, settings.dbx_token or None)
    sql_factory = sql_factory or (lambda token: ConnectorSqlClient(settings.dbx_host, http_path, token))
    cache = Path(settings.local_cache_dir)
    blobs = VolumeBlob(files, settings.dbx_volume_path, cache / "scratch")
    series = VolumeSeriesStore(files, settings.dbx_volume_path, LocalSeriesStore(cache / "parquet", settings.slice_rows, settings.duckdb_memory_limit))
    uploads = BlobUploadCatalog(blobs)
    upload_source = BlobFileSource(blobs, uploads, cache / "scratch")

    def user_factory(token: str):
        sql = sql_factory(token)
        kc = DbxCatalog(sql, settings.dbx_metadata_table)
        return MergedCatalog([kc, uploads]), RoutingSource(DbxSource(sql, kc, settings.dbx_kvt_table), upload_source)

    # without a user (start-up work such as retention) only the app-level files are visible
    return dict(catalog=uploads, source=upload_source, blobs=blobs, series=series, user_factory=user_factory)
