"""Measurements from Delta tables in local folders: the metadata table and the KVT (RI-03, RI-05).

Same questions as the Databricks adapters ask a SQL warehouse (see core/kvt.py); here `deltalake` reads the folders directly.
A KVT partitioned by uuid is read by partition pruning (RI-5 'Tempo di lettura di una misura dalla KVT').
"""
from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.dataset as ds

from viewer.core.kvt import Filters, MetaRow, info_matches, info_of, meta_from_row, source_data  # noqa: F401
from viewer.core.models import IngestError, MeasurementInfo, SourceData


class DeltaFolders:
    """Reads the two tables; opens them lazily so that a missing folder only matters when it is needed."""

    def __init__(self, metadata_dir: Path | None, kvt_dir: Path | None):
        self.metadata_dir = Path(metadata_dir) if metadata_dir else None
        self.kvt_dir = Path(kvt_dir) if kvt_dir else None

    def _table(self, folder: Path):
        from deltalake import DeltaTable  # imported here: only needed when Delta folders are configured

        return DeltaTable(str(folder))

    def metadata(self) -> list[MetaRow]:
        if not self.metadata_dir or not (self.metadata_dir / "_delta_log").is_dir():
            return []
        t = self._table(self.metadata_dir).to_pyarrow_table()
        return [meta_from_row(r) for r in t.to_pylist()]

    def kvt(self, uuid: str) -> pa.Table:
        if not self.kvt_dir or not (self.kvt_dir / "_delta_log").is_dir():
            raise IngestError(f"{uuid}: the KVT table is not available")
        dataset = self._table(self.kvt_dir).to_pyarrow_dataset()
        return dataset.to_table(columns=["key", "value", "timestamp"], filter=ds.field("uuid") == uuid)


class KvtCatalog:
    """MeasurementCatalog over the metadata table (id = uuid)."""

    def __init__(self, tables: DeltaFolders):
        self._t = tables

    def rows(self, filters: Filters | None = None) -> list[MetaRow]:
        return sorted((m for m in self._t.metadata() if m.matches(filters)), key=lambda m: m.start, reverse=True)

    def list(self, filters: Filters | None = None) -> list[MeasurementInfo]:
        return [info_of(m) for m in self.rows(filters)]

    def get(self, measurement_id: str) -> MeasurementInfo | None:
        return next((info_of(m) for m in self._t.metadata() if m.uuid == measurement_id), None)

    def row(self, measurement_id: str) -> MetaRow | None:
        return next((m for m in self._t.metadata() if m.uuid == measurement_id), None)


class KvtSource:
    """MeasurementSource over the KVT: the whole measurement as one frame (the table is read by uuid, in blocks internally)."""

    def __init__(self, tables: DeltaFolders, catalog: KvtCatalog):
        self._t, self._c = tables, catalog

    def has(self, measurement_id: str) -> bool:
        return self._c.row(measurement_id) is not None

    def read(self, measurement_id: str) -> SourceData:
        m = self._c.row(measurement_id)
        if m is None:
            raise KeyError(measurement_id)
        table = self._t.kvt(measurement_id)
        if table.num_rows == 0:
            raise IngestError(f"{m.path or measurement_id}: the KVT has no rows for this measurement")
        return source_data(m, table)
