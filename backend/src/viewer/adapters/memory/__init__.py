"""In-memory implementations of the four interfaces, for tests (RI-36)."""
from __future__ import annotations

import numpy as np

from viewer.core.models import Block, IngestError, MeasurementInfo, SourceData


class MemoryCatalog:
    def __init__(self, items: list[MeasurementInfo] | None = None):
        self._items = {m.id: m for m in items or []}

    def add(self, info: MeasurementInfo) -> None:
        self._items[info.id] = info

    def list(self, filters=None) -> list[MeasurementInfo]:
        from viewer.adapters.local.kvt import info_matches

        return sorted((m for m in self._items.values() if info_matches(m, filters)), key=lambda m: m.name)

    def get(self, measurement_id: str) -> MeasurementInfo | None:
        return self._items.get(measurement_id)


class MemorySource:
    def __init__(self, data: dict[str, SourceData] | None = None):
        self._data = data or {}

    def add(self, measurement_id: str, data: SourceData) -> None:
        self._data[measurement_id] = data

    def read(self, measurement_id: str) -> SourceData:
        try:
            d = self._data[measurement_id]
        except KeyError:
            raise KeyError(measurement_id) from None
        if isinstance(d, Exception):
            raise d
        return SourceData(frame=d.frame.copy(), meta=dict(d.meta))


class MemoryBlob:
    def __init__(self):
        self._d: dict[str, bytes] = {}

    def write(self, key: str, data: bytes) -> None:
        self._d[key] = bytes(data)

    def read(self, key: str) -> bytes:
        return self._d[key]

    def exists(self, key: str) -> bool:
        return key in self._d

    def list(self, prefix: str = "") -> list[str]:
        return sorted(k for k in self._d if k.startswith(prefix))

    def delete(self, key: str) -> None:
        self._d.pop(key, None)

    def move(self, src_key: str, dst_key: str) -> None:
        self._d[dst_key] = self._d.pop(src_key)

    def size(self, key: str) -> int:
        return len(self._d[key])

    def compose(self, dest_key: str, part_keys: list[str]) -> str:
        return _compose(self._d, dest_key, part_keys)


def _compose(store: dict, dest_key: str, part_keys: list[str]) -> str:
    import hashlib

    h = hashlib.sha256()
    out = bytearray()
    for k in part_keys:
        h.update(store[k])
        out += store[k]
    store[dest_key] = bytes(out)
    return h.hexdigest()


class _MemoryWriter:
    def __init__(self, store: "MemorySeriesStore", mid: str):
        self._store, self._mid = store, mid
        self._parts: dict[str, list[tuple[np.ndarray, np.ndarray]]] = {}

    def write_block(self, block: Block) -> None:
        self._parts.setdefault(block.channel, []).append((block.t.copy(), block.value.copy()))

    def commit(self) -> None:
        out = {}
        for ch, parts in self._parts.items():
            t = np.concatenate([p[0] for p in parts])
            v = np.concatenate([p[1] for p in parts])
            order = np.argsort(t, kind="stable")
            out[ch] = (t[order], v[order])
        self._store._data[self._mid] = out

    def abort(self) -> None:
        self._parts.clear()


class MemorySeriesStore:
    def __init__(self):
        self._data: dict[str, dict[str, tuple[np.ndarray, np.ndarray]]] = {}

    def begin_write(self, measurement_id: str) -> _MemoryWriter:
        return _MemoryWriter(self, measurement_id)

    def exists(self, measurement_id: str) -> bool:
        return measurement_id in self._data

    def channels(self, measurement_id: str) -> list[str]:
        return sorted(self._data.get(measurement_id, {}))

    def read_channel(self, measurement_id, channel, t_from_us=None, t_to_us=None):
        t, v = self._data.get(measurement_id, {}).get(
            channel, (np.empty(0, np.int64), np.empty(0, np.float64))
        )
        lo = 0 if t_from_us is None else int(np.searchsorted(t, t_from_us, side="left"))
        hi = t.size if t_to_us is None else int(np.searchsorted(t, t_to_us, side="right"))
        return t[lo:hi].copy(), v[lo:hi].copy()

    def delete(self, measurement_id: str) -> None:
        self._data.pop(measurement_id, None)

    def size(self, measurement_id: str) -> int:
        return sum(t.nbytes + v.nbytes for t, v in self._data.get(measurement_id, {}).values())

    def ids(self) -> list[str]:
        return sorted(self._data)


__all__ = ["MemoryCatalog", "MemorySource", "MemoryBlob", "MemorySeriesStore", "IngestError"]
