"""Export of the data on screen (RF-35): long format (time_s, channel, value), every channel on its own grid, nothing filled.

Rows exist only where the channel has a value: holes of the grid produce no row. time_s comes from the integer lattice index
(k · 0.1 s), so shifts and window edges never accumulate floating-point error.
"""
from __future__ import annotations

import io
import os
import tempfile
from dataclasses import dataclass
from typing import Iterator

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from viewer.core.grid import DELTA_US
from viewer.service import measurements as uc
from viewer.service.context import Services
from viewer.service.results import UnknownHandle

COLUMNS = ("time_s", "channel", "value")
PIECE = 500_000  # rows per written piece


@dataclass
class Piece:
    label: str
    k: np.ndarray  # int64, primary lattice index
    value: np.ndarray  # float64, no NaN


def _window(first_k: np.ndarray, k_from: int | None, k_to: int | None, value: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    keep = ~np.isnan(value)
    if k_from is not None:
        keep &= first_k >= k_from
    if k_to is not None:
        keep &= first_k <= k_to
    return first_k[keep], value[keep]


def collect(svc: Services, series: list[dict], k_from: int | None, k_to: int | None, shifts: dict[str, int]) -> list[Piece]:
    """Resolves every requested series (raises before anything is sent: unknown channel, measurement or handle)."""
    out: list[Piece] = []
    for s in series:
        if s.get("handle"):
            r = svc.results.get(s["handle"], svc.user) if svc.results else None
            if r is None:
                raise UnknownHandle(s["handle"])
            k = (np.arange(r.values.size, dtype=np.int64) + r.j_start) * r.n
            vals = r.values
        else:
            g = uc.get_series(svc, s["file"], [(s["channel"], s.get("mode"))])["series"][0]
            vals = g["values"]
            k = np.arange(vals.size, dtype=np.int64) * g["n"] + int(shifts.get(s["file"], 0))
        kk, vv = _window(k, k_from, k_to, np.asarray(vals, dtype=np.float64))
        out.append(Piece(s["label"], kk, vv))
    return out


def _table(label: str, k: np.ndarray, v: np.ndarray) -> pa.Table:
    return pa.table({"time_s": k.astype(np.float64) / (1_000_000 // DELTA_US), "channel": pa.array([label] * k.size, pa.string()), "value": v})


def _pieces(pieces: list[Piece]) -> Iterator[pa.Table]:
    for p in pieces:
        for a in range(0, p.k.size, PIECE):
            yield _table(p.label, p.k[a : a + PIECE], p.value[a : a + PIECE])


def _quote(label: str, delimiter: str) -> str:
    special = (delimiter, chr(34), chr(10), chr(13))
    return chr(34) + label.replace(chr(34), chr(34) * 2) + chr(34) if any(c in label for c in special) else label


def csv_chunks(pieces: list[Piece], decimal: str = ".", delimiter: str = ",") -> Iterator[bytes]:
    """CSV with a header; `decimal`/`delimiter` let a spreadsheet in another locale open it directly.
    Lines are built column-wise with Arrow (fast); only labels that need it are quoted."""
    if decimal == delimiter:
        raise ValueError("The decimal separator and the delimiter must differ")
    yield (delimiter.join(COLUMNS) + chr(10)).encode()
    for p in pieces:
        label = pa.scalar(_quote(p.label, delimiter), pa.string())
        for a in range(0, p.k.size, PIECE):
            k = p.k[a : a + PIECE]
            t = pc.cast(pa.array(k.astype(np.float64) / (1_000_000 // DELTA_US)), pa.string())
            v = pc.cast(pa.array(p.value[a : a + PIECE]), pa.string())
            if decimal != ".":
                t, v = (pc.replace_substring(x, ".", decimal) for x in (t, v))
            lines = pc.binary_join_element_wise(t, label, v, delimiter)
            yield (chr(10).join(lines.to_pylist()) + chr(10)).encode()


def parquet_file(pieces: list[Piece]) -> str:
    """Writes the Parquet file to a temporary path (the caller deletes it)."""
    schema = pa.schema([("time_s", pa.float64()), ("channel", pa.string()), ("value", pa.float64())])
    fd, path = tempfile.mkstemp(suffix=".parquet")
    os.close(fd)
    with pq.ParquetWriter(path, schema, compression="zstd") as w:
        wrote = False
        for t in _pieces(pieces):
            w.write_table(t)
            wrote = True
        if not wrote:
            w.write_table(schema.empty_table())
    return path
