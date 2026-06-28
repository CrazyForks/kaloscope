"""Recursive data structure utilities."""

from collections.abc import Mapping, MutableMapping
from typing import Any, Literal


def deep_strip(
    obj: Any,
    side: Literal["both", "left", "right"] = "both",
    chars: str | None = None,
) -> Any:
    """Recursively strip strings from an object.

    Args:
        obj: The object to strip.
        side: The side to strip from.
        chars: The characters to strip.

    Returns:
        The stripped object.
    """
    if isinstance(obj, str):
        if side == "both":
            obj = obj.strip(chars)
        elif side == "left":
            obj = obj.lstrip(chars)
        elif side == "right":
            obj = obj.rstrip(chars)
        return obj
    elif isinstance(obj, set | list | tuple):
        return type(obj)(deep_strip(i, side, chars) for i in obj)
    elif isinstance(obj, dict):
        return {k: deep_strip(v, side, chars) for k, v in obj.items()}
    return obj


def deep_update(m1: MutableMapping, m2: Mapping) -> MutableMapping:
    """Recursively update a mapping with another mapping.

    Args:
        m1: The mapping to update.
        m2: The mapping to update with.

    Returns:
        The updated mapping.
    """
    for k, v2 in m2.items():
        v1 = m1.get(k)
        if isinstance(v1, MutableMapping) and isinstance(v2, Mapping):
            m1[k] = deep_update(v1, v2)
        elif isinstance(v1, list) and isinstance(v2, list):
            m1[k] = [*v1, *[x for x in v2 if x not in v1]]
        elif isinstance(v1, set) and isinstance(v2, set):
            m1[k] = v1 | v2
        else:
            m1[k] = v2
    return m1
