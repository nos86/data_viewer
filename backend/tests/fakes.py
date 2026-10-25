"""Stand-ins for the two Databricks clients, so that the adapters are tested without a workspace.

`FakeWarehouse` runs the *real* SQL strings of core/kvt.py on DuckDB over Arrow tables: a wrong statement fails here too.
Unity Catalog permissions are simulated by the grants of each token (which measurement uuids its metadata/KVT rows show).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.compute as pc


class FakeWarehouse:
    def __init__(self, metadata: pa.Table, kvt: pa.Table, grants: dict[str, set[str] | str]):
        self.metadata, self.kvt, self.grants = metadata, kvt, grants
        self.log: list[tuple[str, str, list]] = []  # (token, sql, params)

    def client(self, token: str) -> "FakeSql":
        return FakeSql(self, token)

    def count(self, needle: str) -> int:
        return sum(1 for _, sql, _ in self.log if needle in sql)


class FakeSql:
    def __init__(self, wh: FakeWarehouse, token: str):
        self.wh, self.token = wh, token

    def _visible(self, table: pa.Table) -> pa.Table:
        g = self.wh.grants.get(self.token, set())
        if g == "*":
            return table
        return table.filter(pc.is_in(table["uuid"], pa.array(sorted(g), pa.string())))

    def query(self, sql: str, params: list[Any] | None = None) -> pa.Table:
        self.wh.log.append((self.token, sql, list(params or [])))
        con = duckdb.connect()
        con.register("meta", self._visible(self.wh.metadata))
        con.register("kvt", self._visible(self.wh.kvt))
        con.execute(sql, params or [])
        return con.to_arrow_table()


class LocalFiles:
    """FilesClient over a directory: the volume path /Volumes/c/s/v/x is <root>/Volumes/c/s/v/x."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.calls: list[str] = []

    def _p(self, path: str) -> Path:
        return self.root / path.lstrip("/")

    def upload_bytes(self, path: str, data: bytes) -> None:
        self.calls.append("upload_bytes")
        p = self._p(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def upload_file(self, path: str, local: Path) -> None:
        self.calls.append("upload_file")
        p = self._p(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(Path(local).read_bytes())

    def download_bytes(self, path: str) -> bytes:
        self.calls.append("download_bytes")
        return self._p(path).read_bytes()

    def download_file(self, path: str, local: Path) -> None:
        self.calls.append("download_file")
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(self._p(path).read_bytes())

    def exists(self, path: str) -> bool:
        return self._p(path).is_file()

    def size(self, path: str) -> int:
        return self._p(path).stat().st_size

    def delete(self, path: str) -> None:
        self._p(path).unlink(missing_ok=True)

    def list_files(self, directory: str) -> list[tuple[str, int]]:
        base = self._p(directory)
        if not base.is_dir():
            return []
        return sorted(("/" + p.relative_to(self.root).as_posix(), p.stat().st_size) for p in base.rglob("*") if p.is_file())
