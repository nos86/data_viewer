"""Dependency bundle handed to the service and API layers (RI-34)."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable

from viewer.config import Settings
from viewer.service.cache import SeriesCache
from viewer.service.results import ResultStore
from viewer.ports import BlobStorage, MeasurementCatalog, MeasurementSource, SeriesStore


@dataclass
class Services:
    settings: Settings
    catalog: MeasurementCatalog
    source: MeasurementSource
    blobs: BlobStorage
    series: SeriesStore
    # starts an ingestion in a separate process (measurement id, user token); replaced in tests
    spawn_ingest: Callable[[str, str | None], None] | None = None
    cache: SeriesCache | None = None
    results: ResultStore | None = None
    touched: dict[str, float] = field(default_factory=dict)  # last use per measurement, in memory only to throttle writes
    # identity (RI-40): present when the adapter reads data with the user's own permissions (Databricks)
    user: str = "-"
    token: str | None = None
    user_factory: Callable[[str], tuple[MeasurementCatalog, MeasurementSource]] | None = None
    permissions: dict = field(default_factory=dict)  # (user, measurement id) -> (time, info) of recent successful checks

    def for_user(self, token: str, user: str | None = None) -> "Services":
        """The same services with catalogue and source bound to this user's token; caches, stores and results stay shared.
        Anything served from a shared cache is first looked up in the user's catalogue, so Unity Catalog decides (RI-40)."""
        if self.user_factory is None:
            return self
        from viewer.adapters.routing import CheckedCatalog

        catalog, source = self.user_factory(token)
        who = user or "-"
        return replace(self, catalog=CheckedCatalog(catalog, who, self.permissions, self.settings.permission_ttl_s), source=source, user=who, token=token)

    # small JSON helpers over BlobStorage
    def put_json(self, key: str, obj: Any) -> None:
        self.blobs.write(key, json.dumps(obj).encode("utf-8"))

    def get_json(self, key: str) -> Any | None:
        try:
            return json.loads(self.blobs.read(key))
        except KeyError:
            return None


def status_key(mid: str) -> str:
    return f"status/{mid}.json"


def meta_key(mid: str) -> str:
    return f"meta/{mid}.json"


def channels_key(mid: str) -> str:
    return f"channels/{mid}.json"


def build_services(settings: Settings) -> Services:
    """Choose the adapter set from configuration (RI-34)."""
    if settings.adapter == "local":
        from viewer.adapters.local import build_local

        svc = Services(settings=settings, **build_local(settings))
    elif settings.adapter == "databricks":
        from viewer.adapters.databricks import build_databricks

        svc = Services(settings=settings, **build_databricks(settings))
    else:
        raise ValueError(f"unknown adapter {settings.adapter!r}")
    from viewer.service.ingest import spawn_process

    svc.cache = SeriesCache(settings.cache_mb * 2**20)
    svc.results = ResultStore(settings.result_mb * 2**20)
    svc.spawn_ingest = lambda mid, token=None: spawn_process(settings, mid, token)
    return svc
