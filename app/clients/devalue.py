"""Decoder for SvelteKit's ``__data.json`` payloads.

SvelteKit serialises server ``load`` results with the *devalue* format: each node's
``data`` is a flat array where index 0 is the root and every object/array stores the
*indices* of its children instead of the values themselves. Negative indices are
sentinels (undefined, NaN, ...). This module turns that back into plain Python values.
"""

from __future__ import annotations

import math
from typing import Any

_SENTINELS: dict[int, Any] = {
    -1: None,  # undefined
    -2: None,  # array hole
    -3: math.nan,
    -4: math.inf,
    -5: -math.inf,
    -6: -0.0,
}


class DevalueError(ValueError):
    pass


def unflatten(values: list[Any]) -> Any:
    if not isinstance(values, list) or not values:
        raise DevalueError("devalue payload must be a non-empty list")
    cache: dict[int, Any] = {}

    def hydrate(index: int) -> Any:
        if index in _SENTINELS:
            return _SENTINELS[index]
        if index in cache:
            return cache[index]
        if not 0 <= index < len(values):
            raise DevalueError(f"index {index} out of range")
        raw = values[index]
        if raw is None or isinstance(raw, (str, int, float, bool)):
            cache[index] = raw
            return raw
        if isinstance(raw, list):
            if raw and isinstance(raw[0], str):
                return _typed(index, raw)
            out: list[Any] = []
            cache[index] = out
            out.extend(hydrate(i) for i in raw)
            return out
        if isinstance(raw, dict):
            obj: dict[str, Any] = {}
            cache[index] = obj
            for key, child in raw.items():
                obj[key] = hydrate(child)
            return obj
        raise DevalueError(f"unsupported value at index {index}: {type(raw).__name__}")

    def _typed(index: int, raw: list[Any]) -> Any:
        tag = raw[0]
        if tag in ("Date", "BigInt", "URL"):
            cache[index] = raw[1]
            return raw[1]
        if tag == "Set":
            out: list[Any] = []
            cache[index] = out
            out.extend(hydrate(i) for i in raw[1:])
            return out
        if tag == "Map":
            obj: dict[Any, Any] = {}
            cache[index] = obj
            for k, v in zip(raw[1::2], raw[2::2]):
                obj[hydrate(k)] = hydrate(v)
            return obj
        if tag == "null":  # Object.create(null)
            obj = {}
            cache[index] = obj
            for k, v in zip(raw[1::2], raw[2::2]):
                obj[k] = hydrate(v)
            return obj
        raise DevalueError(f"unsupported devalue type tag {tag!r}")

    return hydrate(0)
