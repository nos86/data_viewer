import pytest
from fastapi.testclient import TestClient

from conftest import make_services
from viewer.api.app import create_app
from viewer.core.models import MeasurementInfo, SourceData
from viewer.service import measurements as uc
from viewer.service import retention
from viewer.service.ingest import run_ingest

GIB = 1024**3
UPLOAD_BYTES = 5000


class Clock:
    def __init__(self, t=100_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(retention, "_now", c)
    return c


def build(frame, ids=("m1", "m2", "m3"), uploaded=("m2", "m3"), limit_bytes=0):
    """Measurements converted to series; the ones in `uploaded` also have an uploaded binary (the others come from the source folder)."""
    svc = make_services(frame, mid=ids[0], storage_limit_gb=limit_bytes / GIB, pin_ttl_s=300)
    for mid in ids[1:]:
        svc.catalog.add(MeasurementInfo(mid, f"{mid}.bin", f"mem://{mid}"))
        svc.source.add(mid, SourceData(frame.copy(), {"signals": ["a", "b"]}))
    for mid in ids:
        run_ingest(svc, mid)
    for mid in uploaded:
        svc.blobs.write(f"uploads/{mid}.bin", b"x" * UPLOAD_BYTES)
        svc.blobs.write(f"uploads/{mid}.json", b'{"name": "x"}')
    svc.touched.clear()
    for mid in ids:
        svc.blobs.delete(f"usage/{mid}.json")           # start without any record of use
    return svc


def use(svc, mid, t, clock):
    clock.t = t
    retention.touch(svc, mid, force=True)


def test_usage_adds_series_uploads_and_small_blobs(regular_frame, clock):
    svc = build(regular_frame)
    u = retention.usage(svc)
    assert u["measurements"] == 3 and u["uploads_bytes"] == 2 * UPLOAD_BYTES
    assert u["parquet_bytes"] == sum(svc.series.size(m) for m in ("m1", "m2", "m3")) > 0
    assert u["used_bytes"] > u["uploads_bytes"] + u["parquet_bytes"] and u["limit_bytes"] == 0


def test_least_recently_used_goes_first_until_under_the_limit(regular_frame, clock):
    svc = build(regular_frame)
    for mid, t in (("m1", 1000), ("m2", 2000), ("m3", 3000)):
        use(svc, mid, t, clock)
    clock.t = 100_000
    total = retention.usage(svc)["used_bytes"]
    one = {e.id: e.total for e in retention.entries(svc)}
    svc.settings = type(svc.settings)(**{**svc.settings.to_dict(), "storage_limit_gb": (total - one["m1"] + 1) / GIB})
    assert retention.enforce(svc) == ["m1"]
    assert svc.series.ids() == ["m2", "m3"]
    svc.settings = type(svc.settings)(**{**svc.settings.to_dict(), "storage_limit_gb": (one["m3"] + 1) / GIB})
    assert retention.enforce(svc) == ["m2"]               # m3 is the most recent and the only one that fits


def test_measurements_in_use_are_never_evicted(regular_frame, clock):
    svc = build(regular_frame, limit_bytes=1)             # an impossible limit
    use(svc, "m1", 1000, clock)
    use(svc, "m2", 2000, clock)
    use(svc, "m3", 99_900, clock)                          # used 100 s ago: inside the 300 s window (a browser with it open)
    clock.t = 100_000
    assert retention.enforce(svc) == ["m1", "m2"]
    assert svc.series.ids() == ["m3"] and svc.blobs.exists("uploads/m3.bin")


def test_a_measurement_without_any_record_counts_as_the_oldest(regular_frame, clock):
    svc = build(regular_frame, limit_bytes=1)
    use(svc, "m2", 5000, clock)
    use(svc, "m3", 99_999, clock)
    clock.t = 100_000
    assert retention.enforce(svc)[0] == "m1"


def test_source_folder_files_are_never_deleted_only_their_cache(regular_frame, clock):
    svc = build(regular_frame, limit_bytes=1)
    for mid in ("m1", "m2", "m3"):
        use(svc, mid, 1000, clock)
    clock.t = 100_000
    assert set(retention.enforce(svc)) == {"m1", "m2", "m3"}
    assert svc.catalog.get("m1") is not None                                   # still listed: it is converted again when opened
    assert not svc.blobs.exists("status/m1.json") and svc.series.ids() == []
    assert not svc.blobs.exists("uploads/m2.bin") and not svc.blobs.exists("uploads/m2.json")   # uploaded binaries are gone
    with pytest.raises(uc.NotReady):
        uc.get_series(svc, "m1", [("a", None)])


def test_no_limit_means_no_eviction(regular_frame, clock):
    svc = build(regular_frame, limit_bytes=0)
    clock.t = 10**9
    assert retention.enforce(svc) == []
    assert len(svc.series.ids()) == 3


def test_nothing_is_evicted_when_under_the_limit(regular_frame, clock):
    svc = build(regular_frame, limit_bytes=10**9)
    clock.t = 10**9
    assert retention.enforce(svc) == []


def test_eviction_drops_cached_grids_of_that_measurement(regular_frame, clock):
    svc = build(regular_frame, limit_bytes=1)
    uc.get_series(svc, "m1", [("a", "step")])
    assert len(svc.cache) == 1
    use(svc, "m1", 1000, clock)
    use(svc, "m2", 1000, clock)
    use(svc, "m3", 1000, clock)
    clock.t = 100_000
    retention.enforce(svc)
    assert len(svc.cache) == 0


def test_every_use_is_recorded_but_writes_are_throttled(regular_frame, clock):
    svc = build(regular_frame)
    uc.get_channels(svc, "m1")
    first = retention.last_used(svc, "m1")
    assert first == clock.t
    clock.t += 5
    uc.get_channels(svc, "m1")
    assert svc.get_json("usage/m1.json")["last_used"] == first            # within the throttle window: no new write
    clock.t += 60
    uc.get_channels(svc, "m1")
    assert svc.get_json("usage/m1.json")["last_used"] == clock.t


def test_api_storage_and_heartbeat(regular_frame, clock):
    svc = build(regular_frame, limit_bytes=10**9)
    c = TestClient(create_app(services=svc))
    s = c.get("/api/v1/storage").json()
    assert s["measurements"] == 3 and s["limit_bytes"] == 10**9 and s["used_bytes"] > 0
    clock.t = 150_000
    assert c.post("/api/v1/sessions/pin", json={"ids": ["m1", "nope"]}).status_code == 204
    assert retention.last_used(svc, "m1") == 150_000
    assert svc.get_json("usage/nope.json") is None                           # unknown ids are ignored
    assert c.post("/api/v1/sessions/pin", json={"ids": ["x"] * 100}).status_code == 422
