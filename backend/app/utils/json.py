"""JSON serialization and parsing utilities."""

import decimal
import re
from functools import partial
from pathlib import Path
from typing import Any

import orjson
from sanic.log import Colors, logger

from app.core.constants import ENCODING

type JSONType = dict[str, JSONType] | list[JSONType] | str | int | float | bool | None

COMMENTS_PATTERN = re.compile(r'//.*?$|/\*.*?\*/|"(?:\\.|[^\\"])*"', re.S | re.M)
TRAILING_COMMAS_PATTERN = re.compile(r',\s*([\]}])|"(?:\\.|[^\\"])*"', re.S)


def default(obj):
    """Default JSON serializer for objects not serializable by orjson.

    See https://github.com/ijl/orjson?tab=readme-ov-file#default for more details.

    Args:
        obj: The object to serialize.

    Returns:
        The serialized object.
    """
    if isinstance(obj, set):
        return list(obj)
    if isinstance(obj, Path):
        return str(obj.resolve())
    if isinstance(obj, decimal.Decimal):
        return str(obj)
    return None


dumps = partial(orjson.dumps, default=default)
loads = orjson.loads


def try_loads(string: str, *, with_comments: bool = False) -> JSONType:
    """Try to deserializes a JSON string into a Python object.

    Args:
        string: The JSON string to deserialize.
        with_comments: Whether to support comments in the JSON string.

    Returns:
        The deserialized Python object if successful, None otherwise.
    """

    # https://code.visualstudio.com/docs/languages/json#_json-with-comments
    if with_comments:
        # remove comments
        string = COMMENTS_PATTERN.sub(
            lambda m: "" if (s := m.group(0)).startswith("/") else s,
            string,
        )
        # remove trailing commas
        string = TRAILING_COMMAS_PATTERN.sub(
            lambda m: m.group(1) if (s := m.group(0)).startswith(",") else s,
            string,
        )

    try:
        return loads(string)
    except orjson.JSONDecodeError:
        logger.debug(
            f"Failed to deserialize the JSON string: {Colors.RED}%s{Colors.END}", string
        )
        return None


def escape(string: str) -> str:
    """Escape special characters in a string.

    Args:
        string: The string to escape.

    Returns:
        The escaped string.
    """
    return dumps(string).decode(ENCODING)[1:-1]


def pretty(obj: Any) -> str:
    """Serialize an object to a pretty-printed JSON string.

    Args:
        obj: The object to serialize.

    Returns:
        The pretty-printed JSON string.
    """
    return dumps(obj, option=orjson.OPT_INDENT_2).decode(ENCODING)
