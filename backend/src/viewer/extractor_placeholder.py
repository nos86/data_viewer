"""Placeholder for the real binary extractor (name and module will be replaced).

Signature: ``extract(path) -> (DataFrame[timestamp, channel, value], metadata dict)``.
In development it reads a fake .bin (JSON with seed/parameters) and calls the mock generator.
"""
from __future__ import annotations

import json

import pandas as pd

from viewer.mock.generator import generate_measurement


def extract(path: str) -> tuple[pd.DataFrame, dict]:
    try:
        with open(path, "rb") as f:
            params = json.loads(f.read().decode("utf-8"))
        if params.get("format") != "mock-bin-v1":
            raise ValueError("unknown format")
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as e:
        raise ValueError(f"not a recognised measurement file ({e})") from e
    params.setdefault("path", path)
    return generate_measurement(params)
