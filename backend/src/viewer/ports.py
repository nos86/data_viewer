"""The four data-base interfaces (RI-33). Core and service depend only on these.

Only app structures cross them (RI-35): dataclasses from core.models, DataFrames, numpy arrays, bytes.
"""
from __future__ import annotations

from typing import Protocol

import numpy as np

from viewer.core.kvt import Filters
from viewer.core.models import Block, MeasurementInfo, SourceData


class MeasurementCatalog(Protocol):
    def list(self, filters: Filters | None = None) -> list[MeasurementInfo]:
        """Measurements, newest first; `filters` is the selector of RF-01 (vin, configurationId, start of sampling)."""
        ...

    def get(self, measurement_id: str) -> MeasurementInfo | None: ...


class MeasurementSource(Protocol):
    def read(self, measurement_id: str) -> SourceData:
        """Whole measurement: local-time frame (timestamp, channel, value) + extractor metadata.

        Raises KeyError for an unknown id, viewer.core.models.IngestError for unreadable data.
        """
        ...


class BlobStorage(Protocol):
    def write(self, key: str, data: bytes) -> None: ...

    def read(self, key: str) -> bytes:
        """Raises KeyError if missing."""
        ...

    def exists(self, key: str) -> bool: ...

    def list(self, prefix: str = "") -> list[str]: ...

    def delete(self, key: str) -> None: ...

    def move(self, src_key: str, dst_key: str) -> None:
        """Renames a blob, replacing the destination; KeyError if the source is missing."""
        ...

    def size(self, key: str) -> int:
        """Bytes of one blob; KeyError if missing."""
        ...

    def compose(self, dest_key: str, part_keys: list[str]) -> str:
        """Concatenates the parts into dest_key (without holding the whole file in memory) and returns the SHA-256 hex of the result."""
        ...


class SeriesWriter(Protocol):
    def write_block(self, block: Block) -> None:
        """Append samples of one channel; blocks may arrive unsorted and interleaved."""
        ...

    def commit(self) -> None:
        """Sort by (channel, t) and publish; afterwards the measurement is readable."""
        ...

    def abort(self) -> None: ...


class SeriesStore(Protocol):
    def begin_write(self, measurement_id: str) -> SeriesWriter: ...

    def exists(self, measurement_id: str) -> bool: ...

    def channels(self, measurement_id: str) -> list[str]: ...

    def read_channel(
        self, measurement_id: str, channel: str, t_from_us: int | None = None, t_to_us: int | None = None
    ) -> tuple[np.ndarray, np.ndarray]:
        """(t int64 µs UTC, value float64) sorted by t, t in [t_from, t_to]; empty if unknown channel."""
        ...

    def delete(self, measurement_id: str) -> None: ...

    def size(self, measurement_id: str) -> int:
        """Bytes used by the stored series (0 if absent)."""
        ...

    def ids(self) -> list[str]:
        """Measurements that have stored series."""
        ...
