import numpy as np
import pytest

from viewer.core.grid import DELTA_US, assign_n, j_max, next_n, step_grid


def test_chain():
    seq = [1]
    for _ in range(6):
        seq.append(next_n(seq[-1]))
    assert seq == [1, 2, 10, 20, 100, 200, 1000]


@pytest.mark.parametrize(
    "period_us,n",
    [
        (10_000, 1),       # 100 Hz native: capped at 10 Hz (n=1)
        (100_000, 1),      # 10 Hz
        (100_080, 1),      # 10 Hz with jitter
        (111_111, 1),      # exactly at the 10% tolerance
        (111_112, 2),
        (200_000, 2),      # 5 Hz
        (333_333, 10),     # 3 Hz: outside the chain -> next slower element, no upscaling
        (500_000, 10),     # 2 Hz
        (1_000_000, 10),   # 1 Hz
        (1_003_000, 10),
        (2_000_000, 20),   # 0.5 Hz
        (10_000_000, 100),
        (20_000_000, 200),
    ],
)
def test_assign_n(period_us, n):
    assert assign_n(period_us) == n


def test_assign_n_event_and_unknown():
    assert assign_n(5_000_000, event=True) == 1
    assert assign_n(None) == 1


def test_never_upscales():
    for period in range(1_000, 30_000_000, 37_321):
        n = assign_n(period)
        assert n * DELTA_US * 10 >= 9 * period            # assigned period >= 0.9 native
        prev = [m for m in (1, 2, 10, 20, 100, 200, 1000, 2000) if m < n]
        assert not prev or prev[-1] * DELTA_US * 10 < 9 * period  # and it is the smallest such


def test_j_max():
    assert j_max(1_000_000, 1) == 10
    assert j_max(1_050_000, 10) == 1


def ms(*vals):
    return np.array(vals, dtype=np.int64) * 1000


def test_step_values_and_exact_points():
    t, v = ms(0, 150, 250, 400), np.array([1.0, 2, 3, 4])
    out = step_grid(t, v, n=1, j_start=0, j_end=5, max_gap_us=150_000)
    # tk = 0,100,200,300,400,500 ms; last point is after the last sample -> hole
    assert out[:5].tolist() == [1, 1, 2, 3, 4]
    assert np.isnan(out[5])


def test_single_missing_sample_is_a_hole():
    t = ms(*[100 * i for i in range(10) if i != 5])
    v = np.arange(9, dtype=float)
    out = step_grid(t, v, 1, 0, 9, 150_000)
    assert np.isnan(out[5]) and not np.isnan(out[4]) and not np.isnan(out[6])


def test_long_gap_all_points_null_but_samples_kept():
    t = ms(0, 100, 200, 1200, 1300)
    v = np.array([0.0, 1, 2, 3, 4])
    out = step_grid(t, v, 1, 0, 13, 150_000)
    assert out[:3].tolist() == [0, 1, 2]
    assert np.isnan(out[3:12]).all()
    assert out[12:14].tolist() == [3, 4]


def test_event_channel_holds_value_without_gap_limit():
    t, v = ms(500, 40_000), np.array([7.0, 9.0])
    out = step_grid(t, v, 1, 0, 450, None)
    assert np.isnan(out[:5]).all()                  # before the first event
    assert (out[5:400] == 7).all() and (out[400:] == 9).all()


def test_grid_n10_samples_every_second():
    t = ms(*range(0, 10_000, 100))
    v = np.arange(100, dtype=float)
    out = step_grid(t, v, 10, 0, 9, 150_000)
    assert out.tolist() == [0, 10, 20, 30, 40, 50, 60, 70, 80, 90]


def test_window_selection_and_empty():
    t, v = ms(0, 100, 200), np.array([1.0, 2, 3])
    assert step_grid(t, v, 1, 1, 2, 150_000).tolist() == [2, 3]
    assert step_grid(np.empty(0, np.int64), np.empty(0), 1, 0, 3, None).tolist() != [] 
    assert step_grid(t, v, 1, 5, 4, None).size == 0


# ---- linear and average (step 2) -------------------------------------------------------------------

from viewer.core.grid import average_grid, linear_grid  # noqa: E402


def test_linear_interpolates_between_adjacent_samples():
    t, v = ms(0, 150, 250, 400), np.array([0.0, 3.0, 5.0, 8.0])
    out = linear_grid(t, v, 1, 0, 5, 150_000)
    # tk = 0,100,200,300,400,500 ms
    assert out[:5].tolist() == pytest.approx([0.0, 2.0, 4.0, 6.0, 8.0])
    assert np.isnan(out[5])                                      # right of the last sample


def test_linear_nan_outside_and_exact_points():
    t, v = ms(100, 200, 300), np.array([1.0, 2.0, 3.0])
    out = linear_grid(t, v, 1, 0, 4, 150_000)
    assert np.isnan(out[0]) and out[1:4].tolist() == [1, 2, 3] and np.isnan(out[4])


def test_linear_respects_gaps():
    t = ms(0, 100, 300, 400)
    v = np.array([0.0, 1, 3, 4])
    out = linear_grid(t, v, 1, 0, 4, 150_000)
    assert out[0] == 0 and out[1] == 1 and np.isnan(out[2]) and out[3] == 3 and out[4] == 4


def test_linear_event_channel_has_no_gap_limit():
    t, v = ms(0, 10_000), np.array([0.0, 10.0])
    out = linear_grid(t, v, 1, 0, 100, None)
    assert out[50] == pytest.approx(5.0) and not np.isnan(out).any()


def test_average_window_is_half_open_and_centred():
    # 100 Hz samples 0..999 ms with value = ms; grid n=1 -> window [tk-50ms, tk+50ms)
    t = ms(*range(0, 1000, 10))
    v = np.arange(0, 1000, 10, dtype=float)
    out = average_grid(t, v, 1, 0, 9, 15_000)
    # tk=100ms: samples 50..140 ms (50 included, 150 excluded) -> mean of 50,60,...,140 = 95
    assert out[1] == pytest.approx(95.0)
    # tk=0: window [-50,50): samples 0..40 -> mean 20 (no left-edge extrapolation)
    assert out[0] == pytest.approx(20.0)


def test_average_n10_from_10hz_uses_ten_samples():
    t = ms(*range(0, 5000, 100))
    v = np.arange(50, dtype=float)
    out = average_grid(t, v, 10, 1, 3, 150_000)
    # tk=1000ms: window [500,1500): samples idx 5..14 -> mean 9.5
    assert out[0] == pytest.approx(9.5) and out[1] == pytest.approx(19.5)


def test_average_empty_window_is_null():
    t, v = ms(0, 10_000), np.array([1.0, 2.0])
    out = average_grid(t, v, 1, 20, 40, None)
    assert np.isnan(out).all()


def test_average_ignores_nan_samples_and_respects_gap_rule():
    t = ms(0, 100, 200, 1000, 1100)
    v = np.array([1.0, np.nan, 3.0, 5.0, 7.0])
    out = average_grid(t, v, 1, 0, 11, 150_000)
    assert np.isnan(out[1]) and out[2] == pytest.approx(3.0)                  # the only sample of window 1 is NaN -> empty
    assert np.isnan(out[4:10]).all()                                          # inside the long gap
    assert out[10] == pytest.approx(5.0) and out[11] == pytest.approx(7.0)
