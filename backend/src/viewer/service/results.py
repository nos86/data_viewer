"""Operator results kept for handles (RI-20). No dependencies on the rest of the service layer."""
from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass, field

import numpy as np


class UnknownHandle(Exception):
    """A handle that is not (or no longer) in the result cache: the client recomputes the chain (RI-20)."""

    def __init__(self, handle: str):
        super().__init__(f"Unknown handle {handle!r}: the result is not in the cache, recompute the chain")
        self.handle = handle


@dataclass
class Result:
    handle: str
    n: int  # grid step in lattice units
    j_start: int  # first grid index; the point k = j·n lies on the primary lattice (shifts already applied)
    values: np.ndarray  # float64, NaN = null
    discrete: bool
    coincident: int  # points where every channel input has a value
    operator: str = ""
    users: set = field(default_factory=set)  # who computed it: a handle is only valid for them (RI-40)


class ResultStore:
    """Bounded LRU of results keyed by handle. Only an optimisation (RI-02): a missing handle is recomputed by the client."""

    def __init__(self, max_bytes: int):
        self.max_bytes = max_bytes
        self._d: OrderedDict[str, Result] = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    def get(self, handle: str, user: str | None = None) -> Result | None:
        with self._lock:
            r = self._d.get(handle)
            if r is not None and user is not None and user not in r.users:
                return None  # computed by someone else: this user must compute it (and so be allowed to read the data)
            if r is not None:
                self._d.move_to_end(handle)
            return r

    def put(self, r: Result) -> None:
        r.values.setflags(write=False)
        with self._lock:
            if r.handle in self._d:
                self._d[r.handle].users |= r.users
                self._d.move_to_end(r.handle)
                return
            self._d[r.handle] = r
            self._bytes += r.values.nbytes
            while self._bytes > self.max_bytes and len(self._d) > 1:
                _, old = self._d.popitem(last=False)
                self._bytes -= old.values.nbytes

    def __len__(self) -> int:
        return len(self._d)
