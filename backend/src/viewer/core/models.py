"""Plain data structures shared by core, service and adapters (RI-35)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class MeasurementInfo:
    id: str
    name: str
    path: str
    size_bytes: int | None = None
    vin: str | None = None
    configuration_id: str | None = None
    start: str | None = None  # startOfSampling, local time, ISO 8601
    end: str | None = None


@dataclass
class SourceData:
    """Whole measurement as returned by the extractor (RI-04): local-time naive timestamps."""
    frame: pd.DataFrame  # columns: timestamp, channel, value
    meta: dict[str, Any]


@dataclass
class Block:
    """Samples of one channel; t in integer µs UTC."""
    channel: str
    t: np.ndarray       # int64
    value: np.ndarray   # float64


@dataclass
class ChannelInfo:
    name: str
    samples: int
    t_min_us: int       # relative to measurement t0
    t_max_us: int
    period_us: int | None   # median Δt of native samples
    event: bool
    v_min: float | None
    v_max: float | None
    n: int
    suggested_mode: str

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class IngestStatus:
    id: str
    state: str = "queued"       # queued | running | done | error
    phase: str = ""
    progress: float = 0.0
    message: str | None = None
    warnings: list[str] = field(default_factory=list)
    pid: int | None = None
    started_at: float = 0.0
    t0_us: int | None = None    # absolute UTC µs of the measurement origin
    end_us: int | None = None   # relative to t0
    timings: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict) -> "IngestStatus":
        return cls(**d)


class IngestError(Exception):
    """Conversion aborted; message names file, channel/block and cause (RI-44)."""

    def __init__(self, message: str = "", *, file: str | None = None, channel: str | None = None, block: str | None = None):
        super().__init__(message)
        self.file, self.channel, self.block = file, channel, block
