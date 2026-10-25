import numpy as np
import pandas as pd
import pytest

from viewer.core.models import IngestError
from viewer.service.convert import local_to_utc_us, sort_dedupe_last, to_float


def local_of(utc_iso_start, periods, freq):
    utc = pd.date_range(utc_iso_start, periods=periods, freq=freq, tz="UTC")
    local = utc.tz_convert("Europe/Rome").tz_localize(None).as_unit("us")
    return utc.as_unit("us").asi8, local.to_numpy()


def test_plain_conversion_summer():
    utc, local = local_of("2026-05-12T08:00:00Z", 10, "1s")
    assert (local_to_utc_us(local, "Europe/Rome", "c") == utc).all()


def test_fall_back_hour_is_inferred_from_sample_order():
    utc, local = local_of("2026-10-24T23:30:00Z", 18, "10min")   # 01:30 CEST ... 02:20 CET
    assert len(set(local.tolist())) < len(local)                  # the wall clock repeats
    assert (local_to_utc_us(local, "Europe/Rome", "c") == utc).all()


def test_fall_back_with_repeated_stamps_in_ambiguous_hour():
    utc, local = local_of("2026-10-24T23:30:00Z", 18, "10min")
    local2 = np.insert(local, 8, local[8])
    utc2 = np.insert(utc, 8, utc[8])
    assert (local_to_utc_us(local2, "Europe/Rome", "c") == utc2).all()


def test_inference_failure_is_explicit():
    local = np.array(["2026-10-25T02:30:00"], dtype="datetime64[us]")   # one sample in the repeated hour
    with pytest.raises(IngestError, match="channel 'x'"):
        local_to_utc_us(local, "Europe/Rome", "x")


def test_nonexistent_local_time_is_explicit():
    local = np.array(["2026-03-29T02:30:00"], dtype="datetime64[us]")
    with pytest.raises(IngestError, match="cannot convert"):
        local_to_utc_us(local, "Europe/Rome", "x")


def test_to_float():
    assert to_float(np.array([True, False], dtype=object)).tolist() == [1.0, 0.0]
    assert to_float(np.array([True, False])).tolist() == [1.0, 0.0]
    assert to_float(np.array([1, 2.5, None], dtype=object))[:2].tolist() == [1.0, 2.5]
    assert to_float(np.array(["IDLE", "RUN"], dtype=object)) is None
    assert to_float(np.array([1.0, "x"], dtype=object)) is None


def test_sort_dedupe_last_wins():
    t = np.array([3, 1, 2, 1, 3], dtype=np.int64)
    v = np.array([30.0, 10, 20, 11, 31])
    t2, v2 = sort_dedupe_last(t, v)
    assert t2.tolist() == [1, 2, 3] and v2.tolist() == [11, 20, 31]
