import numpy as np

from conftest import make_services
from viewer.service import measurements as uc
from viewer.service.cache import SeriesCache
from viewer.service.ingest import run_ingest


def arr(n):
    return np.zeros(n, dtype=np.float64)


def test_hit_and_miss_counters():
    c = SeriesCache(10_000)
    calls = []
    f = lambda: calls.append(1) or arr(10)
    a = c.get_or_compute("k", f)
    b = c.get_or_compute("k", f)
    assert a is b and len(calls) == 1 and (c.hits, c.misses) == (1, 1)


def test_lru_eviction_by_bytes():
    c = SeriesCache(3 * 80)             # three arrays of 10 float64
    for k in "abc":
        c.get_or_compute(k, lambda: arr(10))
    c.get_or_compute("a", lambda: arr(10))        # touch a -> b is least recently used
    c.get_or_compute("d", lambda: arr(10))
    assert len(c) == 3 and c.size_bytes == 240
    calls = []
    c.get_or_compute("b", lambda: calls.append(1) or arr(10))   # evicted -> recomputed
    c.get_or_compute("a", lambda: calls.append(2) or arr(10))   # still cached? 'a' was touched later than c,d? evicted by b insertion order
    assert 1 in calls


def test_oversized_value_not_stored():
    c = SeriesCache(100)
    c.get_or_compute("big", lambda: arr(1000))
    assert len(c) == 0 and c.size_bytes == 0


def test_cached_arrays_are_read_only():
    c = SeriesCache(10_000)
    v = c.get_or_compute("k", lambda: arr(4))
    assert not v.flags.writeable


def test_series_key_includes_mode_and_second_request_hits_cache(regular_frame):
    svc = make_services(regular_frame)
    run_ingest(svc, "m1")
    uc.get_series(svc, "m1", [("a", "step")])
    uc.get_series(svc, "m1", [("a", "step")])
    uc.get_series(svc, "m1", [("a", "linear")])
    assert (svc.cache.hits, svc.cache.misses) == (1, 2)
    w = uc.get_series(svc, "m1", [("a", "step")], j_start=10, j_end=12)["series"][0]     # window sliced from cache
    assert w["values"].tolist() == [10.0, 11.0, 12.0] and svc.cache.hits == 2
