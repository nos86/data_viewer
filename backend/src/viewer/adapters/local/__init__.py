"""Local-PC adapters: folder of .bin files, filesystem blobs, Parquet + DuckDB series."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from viewer.core.models import Block, IngestError, MeasurementInfo, SourceData


def file_digest(path: Path, chunk: int = 8 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while data := f.read(chunk):
            h.update(data)
    return h.hexdigest()[:32]


class _FileIndex:
    """id -> (path, display name) of the measurements: *.bin files of the source folder (id = content hash, cached per
    (path, size, mtime): an optimisation only) and uploaded files (id = file stem, name from the sidecar written at upload)."""

    def __init__(self, folder: Path, uploads: Path | None = None):
        self.folder = Path(folder)
        self.uploads = Path(uploads) if uploads else None
        self._cache: dict[tuple[str, int, int], str] = {}
        self._lock = threading.Lock()

    def scan(self) -> dict[str, tuple[Path, str]]:
        out: dict[str, tuple[Path, str]] = {}
        if self.uploads and self.uploads.is_dir():
            for p in sorted(self.uploads.glob("*.bin")):
                name = p.name
                try:
                    name = json.loads(p.with_suffix(".json").read_text(encoding="utf-8")).get("name", name)
                except (OSError, ValueError):
                    pass
                out[p.stem] = (p, name)
        if self.folder.is_dir():
            for p in sorted(self.folder.glob("*.bin")):
                st = p.stat()
                key = (str(p), st.st_size, st.st_mtime_ns)
                with self._lock:
                    digest = self._cache.get(key) or self._cache.setdefault(key, file_digest(p))
                out[digest] = (p, p.name)  # a file present in the source folder wins over an identical upload
        return out


class LocalCatalog:
    def __init__(self, index: _FileIndex):
        self._index = index

    def list(self, filters=None) -> list[MeasurementInfo]:
        if filters is not None and filters.any():
            return []  # files from the source folder / uploads carry no VIN, configuration or sampling dates
        return [self._info(i, p, n) for i, (p, n) in self._index.scan().items()]

    def get(self, measurement_id: str) -> MeasurementInfo | None:
        e = self._index.scan().get(measurement_id)
        return self._info(measurement_id, *e) if e else None

    @staticmethod
    def _info(mid: str, p: Path, name: str) -> MeasurementInfo:
        return MeasurementInfo(id=mid, name=name, path=str(p), size_bytes=p.stat().st_size)


class LocalSource:
    """Binary files through the extractor (placeholder; replaced by the real one later)."""

    def __init__(self, index: _FileIndex):
        self._index = index

    def read(self, measurement_id: str) -> SourceData:
        entry = self._index.scan().get(measurement_id)
        if entry is None:
            raise KeyError(measurement_id)
        path, name = entry
        from viewer import extractor_placeholder  # imported lazily: heavy, and replaced later

        try:
            frame, meta = extractor_placeholder.extract(str(path))
        except Exception as e:  # extractor failures are reported as unrecognised/corrupt files
            raise IngestError(f"{name}: extractor failed: {e}") from e
        return SourceData(frame=frame, meta=meta)


def _replace(src: Path, dst: Path, attempts: int = 50) -> None:
    """os.replace that tolerates a concurrent reader holding the target open (Windows)."""
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.02)


class LocalBlob:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _p(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root.resolve() not in p.parents:
            raise ValueError(f"bad key {key!r}")
        return p

    def write(self, key: str, data: bytes) -> None:
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + f".tmp{os.getpid()}")
        tmp.write_bytes(data)
        _replace(tmp, p)

    def read(self, key: str) -> bytes:
        for i in range(50):
            try:
                return self._p(key).read_bytes()
            except FileNotFoundError:
                raise KeyError(key) from None
            except PermissionError:  # being replaced right now (Windows)
                if i == 49:
                    raise
                time.sleep(0.02)

    def exists(self, key: str) -> bool:
        return self._p(key).is_file()

    def list(self, prefix: str = "") -> list[str]:
        keys = (p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file())
        return sorted(k for k in keys if k.startswith(prefix) and ".tmp" not in k)

    def delete(self, key: str) -> None:
        self._p(key).unlink(missing_ok=True)

    def move(self, src_key: str, dst_key: str) -> None:
        dst = self._p(dst_key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            _replace(self._p(src_key), dst)
        except FileNotFoundError:
            raise KeyError(src_key) from None

    def size(self, key: str) -> int:
        try:
            return self._p(key).stat().st_size
        except FileNotFoundError:
            raise KeyError(key) from None

    def compose(self, dest_key: str, part_keys: list[str]) -> str:
        dest = self._p(dest_key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + f".tmp{os.getpid()}")
        h = hashlib.sha256()
        with open(tmp, "wb") as out:
            for k in part_keys:
                try:
                    with open(self._p(k), "rb") as f:
                        while block := f.read(8 * 1024 * 1024):
                            h.update(block)
                            out.write(block)
                except FileNotFoundError:
                    raise KeyError(k) from None
        _replace(tmp, dest)
        return h.hexdigest()


def _lit(path) -> str:
    """SQL string literal (DuckDB does not accept parameters in COPY targets / SET)."""
    return "'" + str(path).replace("'", "''") + "'"


class _LocalWriter:
    def __init__(self, store: "LocalSeriesStore", mid: str):
        self._store, self._mid = store, mid
        store.root.mkdir(parents=True, exist_ok=True)
        self._tmp = store.root / f"{mid}.unsorted.parquet"
        self._final = store._path(mid)
        self._writer: pq.ParquetWriter | None = None
        self._buf: list[pa.Table] = []
        self._buf_rows = 0

    def write_block(self, block: Block) -> None:
        n = block.t.size
        if n == 0:
            return
        tbl = pa.table(
            {
                "channel": pa.DictionaryArray.from_arrays(
                    np.zeros(n, dtype=np.int32), pa.array([block.channel], pa.string())
                ),
                "t": block.t.astype(np.int64, copy=False),
                "value": block.value.astype(np.float64, copy=False),
            }
        )
        self._buf.append(tbl)
        self._buf_rows += n
        if self._buf_rows >= self._store.slice_rows:
            self._flush()

    def _flush(self) -> None:
        if not self._buf:
            return
        # unify dictionaries: cast to plain strings for the temp file (dictionary applied again on sort)
        tbl = pa.concat_tables([t.cast(pa.schema([("channel", pa.string()), ("t", pa.int64()), ("value", pa.float64())])) for t in self._buf])
        if self._writer is None:
            self._writer = pq.ParquetWriter(self._tmp, tbl.schema, compression="snappy")
        self._writer.write_table(tbl, row_group_size=self._store.slice_rows)
        self._buf, self._buf_rows = [], 0

    def commit(self) -> None:
        self._flush()
        if self._writer is None:  # nothing written
            self._writer = pq.ParquetWriter(
                self._tmp, pa.schema([("channel", pa.string()), ("t", pa.int64()), ("value", pa.float64())])
            )
        self._writer.close()
        self._writer = None
        tmp_sorted = self._final.with_name(self._final.name + ".part")
        spill = self._store.root / f"{self._mid}.spill"
        con = duckdb.connect()
        try:
            con.execute(f"SET memory_limit='{self._store.memory_limit}'")
            con.execute(f"SET temp_directory={_lit(spill)}")
            con.execute(
                f"COPY (SELECT channel, t, value FROM read_parquet({_lit(self._tmp)}) ORDER BY channel, t) "
                f"TO {_lit(tmp_sorted)} (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 122880)"
            )
        finally:
            con.close()
        os.replace(tmp_sorted, self._final)
        self._cleanup()

    def abort(self) -> None:
        if self._writer is not None:
            self._writer.close()
            self._writer = None
        self._cleanup()

    def _cleanup(self) -> None:
        self._tmp.unlink(missing_ok=True)
        spill = self._store.root / f"{self._mid}.spill"
        if spill.is_dir():
            for p in spill.rglob("*"):
                if p.is_file():
                    p.unlink(missing_ok=True)
            try:
                spill.rmdir()
            except OSError:
                pass


class LocalSeriesStore:
    def __init__(self, root: Path, slice_rows: int = 1_000_000, memory_limit: str = "2GB"):
        self.root = Path(root)
        self.slice_rows = slice_rows
        self.memory_limit = memory_limit

    def _path(self, mid: str) -> Path:
        return self.root / f"{mid}.parquet"

    def begin_write(self, measurement_id: str) -> _LocalWriter:
        return _LocalWriter(self, measurement_id)

    def exists(self, measurement_id: str) -> bool:
        return self._path(measurement_id).is_file()

    def channels(self, measurement_id: str) -> list[str]:
        if not self.exists(measurement_id):
            return []
        con = duckdb.connect()
        try:
            rows = con.execute(
                "SELECT DISTINCT channel FROM read_parquet(?) ORDER BY channel", [str(self._path(measurement_id))]
            ).fetchall()
        finally:
            con.close()
        return [r[0] for r in rows]

    def read_channel(self, measurement_id, channel, t_from_us=None, t_to_us=None):
        if not self.exists(measurement_id):
            return np.empty(0, np.int64), np.empty(0, np.float64)
        sql = "SELECT t, value FROM read_parquet(?) WHERE channel = ?"
        params: list = [str(self._path(measurement_id)), channel]
        if t_from_us is not None:
            sql += " AND t >= ?"
            params.append(int(t_from_us))
        if t_to_us is not None:
            sql += " AND t <= ?"
            params.append(int(t_to_us))
        con = duckdb.connect()
        try:
            res = con.execute(sql, params).fetchnumpy()  # file is sorted by (channel, t); order preserved
        finally:
            con.close()
        return res["t"].astype(np.int64, copy=False), res["value"].astype(np.float64, copy=False)

    def delete(self, measurement_id: str) -> None:
        self._path(measurement_id).unlink(missing_ok=True)

    def size(self, measurement_id: str) -> int:
        try:
            return self._path(measurement_id).stat().st_size
        except FileNotFoundError:
            return 0

    def ids(self) -> list[str]:
        return sorted(p.name[: -len(".parquet")] for p in self.root.glob("*.parquet") if not p.name.endswith(".unsorted.parquet")) if self.root.is_dir() else []


def build_local(settings):
    """Construct the four local adapters from Settings."""
    storage = Path(settings.storage_dir)
    index = _FileIndex(settings.source_dir, storage / "blobs" / "uploads")
    files_catalog, files_source = LocalCatalog(index), LocalSource(index)
    catalog, source = files_catalog, files_source
    if settings.metadata_dir or settings.kvt_dir:
        from viewer.adapters.local.kvt import DeltaFolders, KvtCatalog, KvtSource
        from viewer.adapters.routing import MergedCatalog, RoutingSource

        tables = DeltaFolders(settings.metadata_dir, settings.kvt_dir)
        kc = KvtCatalog(tables)
        catalog = MergedCatalog([kc, files_catalog])  # KVT measurements first, then uploads and source-folder binaries
        source = RoutingSource(KvtSource(tables, kc), files_source)
    return dict(
        catalog=catalog,
        source=source,
        blobs=LocalBlob(storage / "blobs"),
        series=LocalSeriesStore(storage / "parquet", settings.slice_rows, settings.duckdb_memory_limit),
    )
