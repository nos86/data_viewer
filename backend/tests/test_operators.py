import numpy as np
import pytest

from viewer.core import formula, operators as ops

NAN = np.nan


def run(name, *inputs, step=0.1, **params):
    op = ops.get(name)
    return ops.call(op, [np.asarray(i, dtype=float) for i in inputs], ops.validate_params(op, params), step)


def same(a, b):
    np.testing.assert_allclose(a, b, equal_nan=True)


def test_registry_metadata():
    cat = {o["name"]: o for o in ops.describe()}
    assert len(cat) >= 18
    for o in cat.values():
        assert o["version"] >= 1 and o["description"] and o["output"] in ("continuous", "discrete")
        assert o["nature"] in ("pointwise", "temporal") and isinstance(o["deprecated"], bool)
        assert all(p["type"] in ("float", "int", "bool") for p in o["params"])
    assert cat["gt"]["output"] == "discrete" and cat["derivative"]["nature"] == "temporal"
    assert cat["magnitude"]["promoted"] and not cat["add"]["promoted"]
    assert [i["name"] for i in cat["select"]["inputs"]] == ["cond", "a", "b"]


def test_registration_rejects_duplicates():
    with pytest.raises(RuntimeError):
        ops.operator("add", 1, "dup", ["x"])(lambda i, p, s: i[0])


def test_unknown_operator_and_arity():
    with pytest.raises(ops.OperatorError, match="Unknown operator"):
        ops.get("nope")
    with pytest.raises(ops.OperatorError, match="takes 2"):
        run("add", [1, 2])


def test_params_defaults_coercion_and_ranges():
    op = ops.get("moving_average")
    assert ops.validate_params(op, {}) == {"window_s": 1.0}
    assert ops.validate_params(op, {"window_s": "2.5"}) == {"window_s": 2.5}
    with pytest.raises(ops.OperatorError, match="outside"):
        ops.validate_params(op, {"window_s": -1})
    with pytest.raises(ops.OperatorError, match="unknown parameter"):
        ops.validate_params(op, {"bogus": 1})
    with pytest.raises(ops.OperatorError, match="must be float"):
        ops.validate_params(op, {"window_s": "x"})


def test_arithmetic_and_null_propagation():
    same(run("add", [1, 2, NAN, 4], [10, NAN, 30, 40]), [11, NAN, NAN, 44])
    same(run("sub", [5, 6], [1, 2]), [4, 4])
    same(run("mul", [2, 3], [4, 5]), [8, 15])
    same(run("neg", [1, -2, NAN]), [-1, 2, NAN])
    same(run("abs", [-1, 2, NAN]), [1, 2, NAN])


def test_division_by_zero_is_null():
    same(run("div", [1, 2, 3], [2, 0, NAN]), [0.5, NAN, NAN])


def test_sqrt_pow_min_max_clip():
    same(run("sqrt", [4, -1, 9]), [2, NAN, 3])
    same(run("pow", [2, 3], exponent=3), [8, 27])
    same(run("pow", [-8, 0], exponent=-1), [-0.125, NAN])
    same(run("min", [1, 5, NAN], [3, 2, 1]), [1, 2, NAN])
    same(run("max", [1, 5], [3, 2]), [3, 5])
    same(run("clip", [-5, 0.5, 5], low=-1, high=1), [-1, 0.5, 1])
    with pytest.raises(ops.OperatorError):
        run("clip", [1], low=2, high=1)


def test_comparisons_and_logic_are_discrete_with_nulls():
    same(run("gt", [1, 2, 3, NAN], [2, 2, 2, 2]), [0, 0, 1, NAN])
    same(run("ge", [1, 2, 3], [2, 2, 2]), [0, 1, 1])
    same(run("lt", [1, 2, 3], [2, 2, 2]), [1, 0, 0])
    same(run("le", [1, 2, 3], [2, 2, 2]), [1, 1, 0])
    same(run("eq", [1, 2, NAN], [1, 3, 1]), [1, 0, NAN])
    same(run("and", [1, 1, 0, NAN], [1, 0, 1, 1]), [1, 0, 0, NAN])
    same(run("or", [0, 1, 0, NAN], [0, 0, 1, 1]), [0, 1, 1, NAN])
    same(run("not", [0, 5, NAN]), [1, 0, NAN])


def test_select():
    same(run("select", [1, 0, NAN, 1], [10, 10, 10, NAN], [20, 20, 20, 20]), [10, 20, NAN, NAN])


def test_derivative_depends_on_step():
    x = [0.0, 1.0, 4.0, 9.0, 16.0]          # t^2 sampled at step 1
    same(run("derivative", x, step=1.0), [1, 2, 4, 6, 7])
    same(run("derivative", x, step=0.5), [2, 4, 8, 12, 14])
    d = run("derivative", [1, NAN, 3, 4, 5], step=1.0)
    assert np.isnan(d[0]) and np.isnan(d[1]) and np.isnan(d[2]) and d[4] == 1.0   # nulls spread to their neighbours


def test_integral_trapezoid_and_gaps():
    same(run("integral", [1, 1, 1, 1], step=2.0), [0, 2, 4, 6])
    same(run("integral", [0, 1, 2], step=1.0, initial=10), [10, 10.5, 12])
    r = run("integral", [1, 1, NAN, 1, 1], step=1.0)
    assert np.isnan(r[2]) and r[1] == 1.0 and r[4] == 2.0                          # intervals touching the null add nothing


def test_moving_average():
    same(run("moving_average", [1, 2, 3, 4, 5], step=1.0, window_s=3), [1.5, 2, 3, 4, 4.5])
    same(run("moving_average", [1, 2, 3], step=1.0, window_s=0.2), [1, 2, 3])        # window of one point
    r = run("moving_average", [1, NAN, 3, 4, 5], step=1.0, window_s=3)
    assert np.isnan(r[0]) and np.isnan(r[1]) and np.isnan(r[2]) and r[3] == 4.0      # a null inside the window gives null
    same(run("moving_average", [2, 4], step=0.5, window_s=1.0), [3, 4])      # even window: one point behind, partial at the end


def test_delay():
    same(run("delay", [1, 2, 3, 4], step=1.0, delay_s=2), [NAN, NAN, 1, 2])
    same(run("delay", [1, 2], step=1.0, delay_s=0), [1, 2])
    same(run("delay", [1, 2], step=1.0, delay_s=10), [NAN, NAN])


def test_operators_do_not_modify_inputs():
    x = np.array([1.0, NAN, 3.0])
    keep = x.copy()
    for name in ("neg", "abs", "sqrt", "derivative", "integral", "moving_average", "delay", "not"):
        run(name, x)
    same(x, keep)


# ---- formula evaluator and the promoted operators (RI-21) -------------------------------------------------------


def reference_inputs(op):
    rng = np.random.default_rng(7)
    out = {}
    for spec in op.inputs:
        v = rng.normal(0, 5, 500)
        v[rng.integers(0, 500, 20)] = NAN
        out[spec.name] = v
    return out


def test_formula_evaluator_chain_and_params():
    doc = {
        "blocks": [
            {"id": "a", "type": "input", "name": "a"},
            {"id": "k", "type": "const", "value": 2},
            {"id": "m", "type": "op", "op": "mul", "inputs": ["a", "k"]},
            {"id": "w", "type": "op", "op": "moving_average", "params": {"window_s": 0.3}, "inputs": ["m"]},
        ],
        "output": "w",
    }
    same(formula.evaluate(doc, {"a": np.array([1.0, 2, 3, 4])}, 0.1), run("moving_average", [2, 4, 6, 8], step=0.1, window_s=0.3))


def test_formula_evaluator_rejects_loops():
    doc = {"blocks": [{"id": "p", "type": "op", "op": "neg", "inputs": ["q"]}, {"id": "q", "type": "op", "op": "neg", "inputs": ["p"]}], "output": "p"}
    with pytest.raises(ops.OperatorError, match="loop"):
        formula.evaluate(doc, {"x": np.zeros(3)})


@pytest.mark.parametrize("name", [o["name"] for o in ops.describe() if o["promoted"]])
def test_promoted_operator_equals_its_source_formula(name):
    """Permanent equivalence test: a formula promoted to an operator must keep giving the same result (RI-21)."""
    op = ops.get(name)
    data = reference_inputs(op)
    expected = formula.evaluate(op.promoted_from, data, 0.1)
    got = ops.call(op, [data[i.name] for i in op.inputs], ops.validate_params(op, {}), 0.1)
    same(got, expected)


def test_every_promoted_operator_documents_its_formula_inputs():
    for o in ops.REGISTRY.values():
        if o.promoted_from:
            names = {b["name"] for b in o.promoted_from["blocks"] if b["type"] == "input"}
            assert names == {i.name for i in o.inputs}
