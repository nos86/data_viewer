"""The two thin clients the Databricks adapters talk through. They are the only code that imports the Databricks libraries (RI-33):
everything else depends on the small protocols below, which tests replace with fakes.

NOT validated against a real workspace in this repository (none was reachable); `scripts/databricks_smoke.py` checks them there.
"""
from __future__ import annotations

import io
import shutil
from pathlib import Path
from typing import Any, Protocol

import pyarrow as pa


class SqlClient(Protocol):
    def query(self, sql: str, params: list[Any] | None = None) -> pa.Table:
        """Runs a statement on the SQL warehouse (`?` positional parameters) and returns the result as an Arrow table."""
        ...


class FilesClient(Protocol):
    """Unity Catalog Volume files (absolute paths such as /Volumes/catalog/schema/volume/viewer/...)."""

    def upload_bytes(self, path: str, data: bytes) -> None: ...

    def upload_file(self, path: str, local: Path) -> None: ...

    def download_bytes(self, path: str) -> bytes: ...

    def download_file(self, path: str, local: Path) -> None: ...

    def exists(self, path: str) -> bool: ...

    def size(self, path: str) -> int: ...

    def delete(self, path: str) -> None: ...

    def list_files(self, directory: str) -> list[tuple[str, int]]:
        """All files below a directory, recursively, as (absolute path, bytes). A missing directory gives []."""
        ...


class ConnectorSqlClient:
    """databricks-sql-connector against a SQL warehouse, with the token of the user (or of the app)."""

    def __init__(self, host: str, http_path: str, token: str):
        self._host = host.removeprefix("https://").removeprefix("http://").rstrip("/")
        self._http_path, self._token = http_path, token

    def query(self, sql: str, params: list[Any] | None = None) -> pa.Table:
        from databricks import sql as dbsql  # only here

        with dbsql.connect(server_hostname=self._host, http_path=self._http_path, access_token=self._token) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params or None)
                return cur.fetchall_arrow()


class SdkFilesClient:
    """databricks-sdk Files API for Volumes."""

    def __init__(self, host: str, token: str | None = None):
        from databricks.sdk import WorkspaceClient  # only here

        # inside a Databricks App the credentials come from the environment (service principal); locally from the token
        self._w = WorkspaceClient(host=host or None, token=token or None)

    def upload_bytes(self, path: str, data: bytes) -> None:
        self._w.files.upload(path, io.BytesIO(data), overwrite=True)

    def upload_file(self, path: str, local: Path) -> None:
        with open(local, "rb") as f:
            self._w.files.upload(path, f, overwrite=True)

    def download_bytes(self, path: str) -> bytes:
        return self._w.files.download(path).contents.read()

    def download_file(self, path: str, local: Path) -> None:
        local.parent.mkdir(parents=True, exist_ok=True)
        with open(local, "wb") as out:
            shutil.copyfileobj(self._w.files.download(path).contents, out, 8 * 1024 * 1024)

    def exists(self, path: str) -> bool:
        try:
            self._w.files.get_metadata(path)
            return True
        except Exception as e:  # NotFound
            if type(e).__name__ in ("NotFound", "ResourceDoesNotExist"):
                return False
            raise

    def size(self, path: str) -> int:
        return int(self._w.files.get_metadata(path).content_length or 0)

    def delete(self, path: str) -> None:
        try:
            self._w.files.delete(path)
        except Exception as e:
            if type(e).__name__ not in ("NotFound", "ResourceDoesNotExist"):
                raise

    def list_files(self, directory: str) -> list[tuple[str, int]]:
        out: list[tuple[str, int]] = []
        try:
            for e in self._w.files.list_directory_contents(directory):
                if e.is_directory:
                    out += self.list_files(e.path)
                else:
                    out.append((e.path, int(e.file_size or 0)))
        except Exception as e:
            if type(e).__name__ not in ("NotFound", "ResourceDoesNotExist"):
                raise
        return out
