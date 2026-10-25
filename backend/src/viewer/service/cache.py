"""LRU cache of computed grids, bounded by memory (RI-14). Only an optimisation (RI-02)."""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Callable, Hashable

import numpy as np


class SeriesCache:
    def __init__(self, max_bytes: int):
        self.max_bytes = max_bytes
        self._d: OrderedDict[Hashable, np.ndarray] = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    @property
    def size_bytes(self) -> int:
        return self._bytes

    def __len__(self) -> int:
        return len(self._d)

    def get_or_compute(self, key: Hashable, compute: Callable[[], np.ndarray]) -> np.ndarray:
        with self._lock:
            hit = self._d.get(key)
            if hit is not None:
                self._d.move_to_end(key)
                self.hits += 1
                return hit
            self.misses += 1
        value = compute()
        value.setflags(write=False)
        with self._lock:
            if key not in self._d and value.nbytes <= self.max_bytes:
                self._d[key] = value
                self._bytes += value.nbytes
                while self._bytes > self.max_bytes:
                    _, old = self._d.popitem(last=False)
                    self._bytes -= old.nbytes
        return value

    def discard(self, match) -> None:
        """Drops the entries whose key satisfies `match`."""
        with self._lock:
            for k in [k for k in self._d if match(k)]:
                self._bytes -= self._d.pop(k).nbytes

    def clear(self) -> None:
        with self._lock:
            self._d.clear()
            self._bytes = 0
