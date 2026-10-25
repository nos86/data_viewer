"""Pure evaluator of the JSON formula format over plain arrays (RF-33, RI-21).

Not used by the server (orchestration lives in the frontend, RI-23); it proves that a promoted operator equals the formula it
came from and is the small starting point for a future batch executor (graphlib, "a few lines").

Document: {"blocks": [{"id", "type": "input", "name"} | {"id", "type": "const", "value"} |
                      {"id", "type": "op", "op", "params", "inputs": [block ids]}], "output": id}
"""
from __future__ import annotations

from graphlib import CycleError, TopologicalSorter
from typing import Any

import numpy as np

from viewer.core import operators as ops


def evaluate(doc: dict, inputs: dict[str, np.ndarray], step_s: float = 0.1) -> np.ndarray:
    blocks = {b["id"]: b for b in doc["blocks"]}
    graph = {bid: set(b.get("inputs", [])) for bid, b in blocks.items() if b["type"] == "op"}
    for bid, b in blocks.items():
        graph.setdefault(bid, set())
    try:
        order = list(TopologicalSorter(graph).static_order())
    except CycleError as e:
        raise ops.OperatorError(f"The formula contains a loop: {e.args[1]}") from None
    shape = next(iter(inputs.values())).shape
    val: dict[str, np.ndarray] = {}
    for bid in order:
        b = blocks[bid]
        if b["type"] == "input":
            val[bid] = np.asarray(inputs[b["name"]], dtype=np.float64)
        elif b["type"] == "const":
            val[bid] = np.full(shape, float(b["value"]))
        elif b["type"] == "op":
            op = ops.get(b["op"])
            params: dict[str, Any] = ops.validate_params(op, b.get("params"))
            val[bid] = ops.call(op, [val[i] for i in b["inputs"]], params, step_s)
        else:
            raise ops.OperatorError(f"Unknown block type {b['type']!r}")
    return val[doc["output"]]
