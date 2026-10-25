import io
import json

import numpy as np
import pandas as pd
import pytest

from conftest import make_services
from viewer.core import operators as ops
from viewer.core.models import MeasurementInfo, SourceData
from viewer.service import operators as svc_ops
from viewer.service.ingest import run_ingest
from viewer.service.results import UnknownHandle


def two(regular_frame):
    """m1 and m2 (same data), both converted."""
    svc = make_services(regular_frame, mid="m1")
    svc.catalog.add(MeasurementInfo("m2", "m2.bin", "mem://m2"))
    svc.source.add("m2", SourceData(regular_frame.copy(), {"signals": ["a", "b"]}))
    run_ingest(svc, "m1")
    run_ingest(svc, "m2")
    return svc


def ch(f, c, mode="step"):
    return {"ch": {"file": f, "channel": c, "mode": mode}}


def apply(svc, name, inputs, params=None, shifts=None):
    return svc_ops.apply(svc, name, inputs, params, shifts)


def test_pointwise_on_one_grid(regular_frame):
    svc = two(regular_frame)
    r = apply(svc, "add", [ch("m1", "a"), {"const": 1}])
    assert r.n == 1 and r.j_start == 0 and r.values.size == 600
    np.testing.assert_allclose(r.values, np.arange(600) + 1)
    assert r.coincident == 600 and not r.discrete


def test_output_grid_is_the_slowest_input_and_faster_ones_are_subsampled(regular_frame):
    svc = two(regular_frame)
    r = apply(svc, "add", [ch("m1", "a"), ch("m1", "b")])    # a: n=1 (value = k), b: n=10 (value = j)
    assert r.n == 10 and r.values.size == 60
    np.testing.assert_allclose(r.values, 10 * np.arange(60) + np.arange(60))


def test_shift_aligns_on_the_primary_lattice(regular_frame):
    svc = two(regular_frame)
    r = apply(svc, "add", [ch("m1", "a"), ch("m2", "a")], shifts={"m2": 5})
    # primary k = own k + 5: m2.a covers k 5..604, m1.a covers 0..599
    assert r.j_start == 0 and r.values.size == 605
    assert np.isnan(r.values[:5]).all() and np.isnan(r.values[600:]).all()
    np.testing.assert_allclose(r.values[5:600], np.arange(5, 600) + np.arange(0, 595))
    assert r.coincident == 595


def test_negative_shift_gives_negative_start(regular_frame):
    svc = two(regular_frame)
    r = apply(svc, "sub", [ch("m1", "a"), ch("m2", "a")], shifts={"m2": -7})
    # m2 starts at k = -7 -> the output starts there too
    assert r.j_start == -7
    assert np.isnan(r.values[7 + 593:]).all()        # m2 ends at 592, m1 continues to 599 (m2 missing there)


def test_shift_not_a_multiple_of_the_grid_step_leaves_no_coincident_points(regular_frame):
    svc = two(regular_frame)
    r = apply(svc, "add", [ch("m1", "b"), ch("m2", "b")], shifts={"m2": 5})   # two 1 Hz grids offset by half a second
    assert r.n == 10 and r.coincident == 0 and np.isnan(r.values).all()


def test_constant_rules(regular_frame):
    svc = two(regular_frame)
    with pytest.raises(ops.OperatorError, match="constants"):
        apply(svc, "add", [{"const": 1}, {"const": 2}])
    ops.operator("tmp_two_input_temporal", 1, "test", ["x", "y"], nature="temporal")(lambda i, p, s: i[0] + i[1])
    try:
        with pytest.raises(ops.OperatorError, match="cannot take a constant"):
            apply(svc, "tmp_two_input_temporal", [ch("m1", "a"), {"const": 1}])
    finally:
        del ops.REGISTRY["tmp_two_input_temporal"]
    with pytest.raises(ops.OperatorError, match="exactly one"):
        apply(svc, "add", [{"const": 1, "handle": "x"}, ch("m1", "a")])
    with pytest.raises(ops.OperatorError, match="not a number"):
        apply(svc, "add", [{"const": "abc"}, ch("m1", "a")])


def test_null_propagation_and_discrete_flag(regular_frame):
    f = regular_frame[~((regular_frame.channel == "a") & (regular_frame.value == 100))].reset_index(drop=True)
    svc = two(f)
    r = apply(svc, "gt", [ch("m1", "a"), {"const": 300}])
    assert r.discrete and np.isnan(r.values[100]) and r.values[299] == 0 and r.values[301] == 1


def test_handle_is_deterministic_and_sensitive_to_every_ingredient(regular_frame):
    svc = two(regular_frame)
    base = apply(svc, "add", [ch("m1", "a"), {"const": 1}]).handle
    assert apply(svc, "add", [ch("m1", "a"), {"const": 1}]).handle == base
    assert len(base) == 24
    variants = {
        "operator": apply(svc, "sub", [ch("m1", "a"), {"const": 1}]).handle,
        "constant": apply(svc, "add", [ch("m1", "a"), {"const": 2}]).handle,
        "channel": apply(svc, "add", [ch("m1", "b"), {"const": 1}]).handle,
        "file": apply(svc, "add", [ch("m2", "a"), {"const": 1}]).handle,
        "mode": apply(svc, "add", [ch("m1", "a", "linear"), {"const": 1}]).handle,
        "shift": apply(svc, "add", [ch("m1", "a"), {"const": 1}], shifts={"m1": 3}).handle,
        "order": apply(svc, "add", [{"const": 1}, ch("m1", "a")]).handle,
    }
    assert len(set(variants.values()) | {base}) == len(variants) + 1
    # shift of a file that is not an input does not change the handle
    assert apply(svc, "add", [ch("m1", "a"), {"const": 1}], shifts={"m2": 9}).handle == base
    p1 = apply(svc, "pow", [ch("m1", "a")], {"exponent": 2}).handle
    assert apply(svc, "pow", [ch("m1", "a")]).handle == p1                  # defaults are part of the parameters
    assert apply(svc, "pow", [ch("m1", "a")], {"exponent": 3}).handle != p1
    assert apply(svc, "add", [ch("m1", "a"), {"const": 1}]).handle != apply(svc, "add", [ch("m1", "a", "linear"), {"const": 1}]).handle


def test_default_mode_is_resolved_into_the_handle(regular_frame):
    svc = two(regular_frame)
    h1 = apply(svc, "neg", [{"ch": {"file": "m1", "channel": "a"}}]).handle
    h2 = apply(svc, "neg", [ch("m1", "a", "linear")]).handle                 # 'a' is a regular channel: default = linear
    assert h1 == h2


def test_chain_through_handles_and_unknown_handle(regular_frame):
    svc = two(regular_frame)
    r1 = apply(svc, "add", [ch("m1", "a"), {"const": 1}])
    r2 = apply(svc, "mul", [{"handle": r1.handle}, {"const": 2}])
    np.testing.assert_allclose(r2.values, (np.arange(600) + 1) * 2)
    r3 = apply(svc, "moving_average", [{"handle": r2.handle}], {"window_s": 0.3})
    assert r3.values.size == 600 and r3.n == 1
    with pytest.raises(UnknownHandle) as e:
        apply(svc, "neg", [{"handle": "deadbeef"}])
    assert e.value.handle == "deadbeef"
    svc.results._d.clear()                                  # the cache is only an optimisation: handles can vanish
    with pytest.raises(UnknownHandle):
        apply(svc, "mul", [{"handle": r1.handle}, {"const": 2}])


def test_result_store_is_bounded_lru():
    from viewer.service.results import Result, ResultStore

    st = ResultStore(3 * 80)
    for h in "abcd":
        st.put(Result(h, 1, 0, np.zeros(10), False, 0))
    assert len(st) == 3 and st.get("a") is None and st.get("d") is not None
    st.get("b")
    st.put(Result("e", 1, 0, np.zeros(10), False, 0))
    assert st.get("b") is not None and st.get("c") is None


def test_summary_statistics_and_sparkline(regular_frame):
    svc = two(regular_frame)
    r = apply(svc, "neg", [ch("m1", "a")])
    s = svc_ops.summarise(r)
    assert s["stats"] == {"count": 600, "nulls": 0, "min": -599.0, "max": 0.0, "mean": -299.5, "last": -599.0}
    assert len(s["sparkline"]) == 200 and s["sparkline"][0][0] <= s["sparkline"][0][1]
    f = svc_ops.summarise(svc_ops.Result("x", 1, 0, np.full(5, np.nan), False, 0))
    assert f["stats"]["count"] == 0 and f["stats"]["min"] is None and f["sparkline"][0] == [None, None]


def test_inputs_must_overlap(regular_frame):
    svc = two(regular_frame)
    r = apply(svc, "add", [ch("m1", "a"), ch("m2", "a")], shifts={"m2": 100_000})
    assert r.coincident == 0   # union covers both; nothing coincides
