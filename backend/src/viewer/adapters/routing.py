"""Combines several sources of measurements behind the same interfaces (RI-33): Delta tables first, then uploads and source-folder files."""
from __future__ import annotations

from viewer.core.kvt import Filters
from viewer.core.models import MeasurementInfo, SourceData
from viewer.ports import MeasurementCatalog, MeasurementSource


class MergedCatalog:
    def __init__(self, catalogs: list[MeasurementCatalog]):
        self._cats = catalogs

    def list(self, filters: Filters | None = None) -> list[MeasurementInfo]:
        out: dict[str, MeasurementInfo] = {}
        for c in self._cats:
            for m in c.list(filters):
                out.setdefault(m.id, m)  # the first catalogue that knows an id wins
        return list(out.values())

    def get(self, measurement_id: str) -> MeasurementInfo | None:
        for c in self._cats:
            m = c.get(measurement_id)
            if m is not None:
                return m
        return None


class RoutingSource:
    """Reads a measurement from the KVT when the metadata table knows it, otherwise through the extractor."""

    def __init__(self, kvt: "object", files: MeasurementSource):
        self._kvt = kvt
        self._files = files

    def read(self, measurement_id: str) -> SourceData:
        if self._kvt.has(measurement_id):  # type: ignore[attr-defined]
            return self._kvt.read(measurement_id)  # type: ignore[attr-defined]
        return self._files.read(measurement_id)


class CheckedCatalog:
    """The user's own view of the catalogue: `get` is the permission check before anything from a shared cache is served (RI-40).
    A granted check is remembered for a short time (shared dict, keyed by user and id) so that polling does not query the warehouse each time."""

    def __init__(self, inner: MeasurementCatalog, user: str, memo: dict, ttl_s: float = 30.0):
        self._inner, self._user, self._memo, self._ttl = inner, user, memo, ttl_s

    def list(self, filters: Filters | None = None) -> list[MeasurementInfo]:
        return self._inner.list(filters)

    def get(self, measurement_id: str) -> MeasurementInfo | None:
        import time

        key = (self._user, measurement_id)
        hit = self._memo.get(key)
        if hit and time.time() - hit[0] < self._ttl:
            return hit[1]
        info = self._inner.get(measurement_id)
        if info is not None:
            self._memo[key] = (time.time(), info)
        else:
            self._memo.pop(key, None)
        return info
