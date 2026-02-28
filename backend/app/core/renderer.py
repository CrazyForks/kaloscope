import base64
import datetime
import re
from functools import partial
from typing import Any, Literal

import jinja2
import opencc
from jinja2.defaults import (
    BLOCK_END_STRING,
    BLOCK_START_STRING,
    VARIABLE_END_STRING,
    VARIABLE_START_STRING,
)
from jsonpath_ng.ext import parse
from lxml import etree

from app.core.constants import ENCODING
from app.utils import json
from app.utils.deep import deep_strip

# create a new Jinja2 environment
# https://jinja.palletsprojects.com/en/stable/api/#jinja2.Environment
ENV = jinja2.Environment(
    block_start_string=BLOCK_START_STRING,
    block_end_string=BLOCK_END_STRING,
    variable_start_string=VARIABLE_START_STRING,
    variable_end_string=VARIABLE_END_STRING,
    trim_blocks=True,
    lstrip_blocks=True,
    finalize=lambda x: x if x is not None else "",
)
ENV.policies["json.dumps_kwargs"] = {"ensure_ascii": False}

# cache for compiled expressions
JSONPATH_CACHE = {}
XPATH_CACHE = {}
REGEX_CACHE = {}

# create converters for traditional and simplified Chinese
S2T_CONVERTER = opencc.OpenCC("s2t.json")
T2S_CONVERTER = opencc.OpenCC("t2s.json")


def compile_jsonpath(expr: str) -> Any:
    """Compile a JSONPath expression.

    See https://github.com/h2non/jsonpath-ng for more details.

    Args:
        expr: The string expression to compile.

    Returns:
        The compiled JSONPath expression object.
    """
    jsonpath = JSONPATH_CACHE.get(expr)
    if jsonpath is None:
        JSONPATH_CACHE[expr] = jsonpath = parse(expr)
    return jsonpath


def compile_xpath(expr: str) -> Any:
    """Compile an XPath expression.

    See https://lxml.de/xpathxslt.html#xpath for more details.

    Args:
        expr: The string expression to compile.

    Returns:
        The compiled XPath expression object.
    """
    xpath = XPATH_CACHE.get(expr)
    if xpath is None:
        XPATH_CACHE[expr] = xpath = etree.XPath(expr)
    return xpath


def compile_regex(expr: str) -> re.Pattern[str]:
    """Compile a regular expression.

    See https://docs.python.org/3/library/re.html for more details.

    Args:
        expr: The string expression to compile.

    Returns:
        The compiled regular expression object.
    """
    pattern = REGEX_CACHE.get(expr)
    if pattern is None:
        REGEX_CACHE[expr] = pattern = re.compile(expr, re.DOTALL | re.MULTILINE)
    return pattern


def trim(obj: Any, chars: str | None = None) -> Any:
    """Strip leading and trailing characters."""
    return deep_strip(obj, chars=chars)


def ltrim(obj: Any, chars: str | None = None) -> Any:
    """Strip leading characters."""
    return deep_strip(obj, "left", chars)


def rtrim(obj: Any, chars: str | None = None) -> Any:
    """Strip trailing characters."""
    return deep_strip(obj, "right", chars)


def json_escape(string: str) -> str:
    """Escape a string for JSON."""
    return json.escape(string)


def jsonpath_first(obj: Any, expr: str) -> Any:
    """Find first match in a JSON string or object using a JSONPath expression."""
    jsonpath = compile_jsonpath(expr)
    if isinstance(obj, str):
        obj = json.try_loads(obj, with_comments=True)
    matches = jsonpath.find(obj)
    return matches[0].value if matches else None


def jsonpath_all(obj: Any, expr: str) -> list:
    """Find all matches in a JSON string or object using a JSONPath expression."""
    jsonpath = compile_jsonpath(expr)
    if isinstance(obj, str):
        obj = json.try_loads(obj, with_comments=True)
    matches = jsonpath.find(obj)
    return [match.value for match in matches]


def xpath_first(obj: Any, expr: str) -> Any:
    """Find first match in an HTML string or object using an XPath expression."""
    xpath = compile_xpath(expr)
    if isinstance(obj, str):
        obj = etree.fromstring(obj, parser=etree.HTMLParser())
    result = xpath(obj)
    if isinstance(result, list):
        return result[0] if result else None
    return result


def xpath_all(obj: Any, expr: str) -> list:
    """Find all matches in an HTML string or object using an XPath expression."""
    xpath = compile_xpath(expr)
    if isinstance(obj, str):
        obj = etree.fromstring(obj, parser=etree.HTMLParser())
    result = xpath(obj)
    if not isinstance(result, list):
        return [result] if result is not None else []
    return result


def regex_first(string: str, expr: str) -> Any:
    """Find first match in a string using a regular expression."""
    pattern = compile_regex(expr)
    match = pattern.search(string)
    return match.group(1) if match else None


def regex_all(string: str, expr: str) -> list:
    """Find all matches in a string using a regular expression."""
    pattern = compile_regex(expr)
    return pattern.findall(string)


_FINDERS = {
    "jsonpath_first": jsonpath_first,
    "jsonpath_all": jsonpath_all,
    "xpath_first": xpath_first,
    "xpath_all": xpath_all,
    "regex_first": regex_first,
    "regex_all": regex_all,
}


def _find(
    obj: Any,
    expr: str,
    limit: int | Literal["first", "all", "auto"] = "auto",
    *,
    expr_type: Literal["jsonpath", "xpath", "regex"],
) -> Any:
    """Find values in an object using an expression.

    Args:
        obj: The object to search.
        expr: The expression to use.
        limit: The limit of results to return.
        expr_type: The type of expression.

    Returns:
        The result of the search.
    """
    auto = limit == "auto"
    slicer = slice(0, None)

    # determine the finder type based on the limit
    finder_type: Literal["first", "all"]
    if isinstance(limit, int):
        if limit <= 1:
            finder_type = "first"
        else:
            finder_type = "all"
            slicer = slice(0, limit)
    elif auto:
        finder_type = "all"
    else:
        finder_type = limit

    # call the appropriate finder function
    finder = _FINDERS[f"{expr_type}_{finder_type}"]
    result = finder(obj, expr)

    # return the result
    if not isinstance(result, list):
        return result
    elif auto:
        length = len(result)
        return None if length == 0 else result[0] if length == 1 else result
    else:
        return result[slicer]


def s2t(string: str) -> str:
    """Convert a string from simplified to traditional Chinese."""
    return S2T_CONVERTER.convert(string)


def t2s(string: str) -> str:
    """Convert a string from traditional to simplified Chinese."""
    return T2S_CONVERTER.convert(string)


def duration(
    value: float, unit: Literal["milliseconds", "seconds", "minutes"] = "milliseconds"
) -> str:
    """Convert a duration to a human-readable format."""
    match unit:
        case "milliseconds":
            d = datetime.timedelta(milliseconds=value)
        case "seconds":
            d = datetime.timedelta(seconds=value)
        case "minutes":
            d = datetime.timedelta(minutes=value)
    total_seconds = int(d.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return (f"{hours:02}:" if hours else "") + f"{minutes:02}:{seconds:02}"


def b64decode(s: str | bytes) -> str:
    """Decode the Base64 encoded bytes or string."""
    return base64.b64decode(s).decode(ENCODING)


def b64encode(s: str | bytes) -> str:
    """Encode the bytes or string to Base64."""
    if isinstance(s, str):
        s = s.encode(ENCODING)
    return base64.b64encode(s).decode(ENCODING)


def ternary[V](value: V, v1: V, v2: V) -> V:
    """Return v1 if value is truthy, otherwise return v2."""
    return v1 if value else v2


# register custom filters
# https://jinja.palletsprojects.com/en/stable/api/#custom-filters
ENV.filters["trim"] = trim
ENV.filters["ltrim"] = ltrim
ENV.filters["rtrim"] = rtrim
ENV.filters["json_escape"] = json_escape
ENV.filters["jsonpath_first"] = jsonpath_first
ENV.filters["jsonpath_all"] = jsonpath_all
ENV.filters["jsonpath"] = partial(_find, expr_type="jsonpath")
ENV.filters["xpath_first"] = xpath_first
ENV.filters["xpath_all"] = xpath_all
ENV.filters["xpath"] = partial(_find, expr_type="xpath")
ENV.filters["regex_first"] = regex_first
ENV.filters["regex_all"] = regex_all
ENV.filters["regex"] = partial(_find, expr_type="regex")
ENV.filters["s2t"] = s2t
ENV.filters["t2s"] = t2s
ENV.filters["duration"] = duration
ENV.filters["b64decode"] = b64decode
ENV.filters["b64encode"] = b64encode
ENV.filters["ifel"] = ternary


def render(value: json.JSONType, context: dict, *, raw: bool = False) -> Any:
    """Render a JSON value with a context.

    Args:
        value: The value to render.
        context: The context to render with.
        raw: Whether to render the value as raw object.

    Returns:
        The rendered value.
    """
    if isinstance(value, str) and is_template(value):
        if raw and (key := raw_key(context, value)) is not None:
            return get_raw(context, key)
        template = ENV.from_string(value)
        return template.render(context)
    elif isinstance(value, list):
        return [render(v, context, raw=raw) for v in value]
    elif isinstance(value, dict):
        return {k: render(v, context, raw=raw) for k, v in value.items()}
    else:
        return value


def raw_key(dictionary: dict, string: str) -> str | None:
    """Check if a template string is a nested key in a dictionary.

    Args:
        dictionary: The dictionary to check.
        string: The template string to check.

    Returns:
        The stripped key if it is in the dictionary, None otherwise.
    """
    key = string.strip()
    if key.startswith(VARIABLE_START_STRING) and key.endswith(VARIABLE_END_STRING):
        key = key[2:-2].strip()
    # check nested keys
    keys = key.split(".")
    data = dictionary
    for k in keys:
        if isinstance(data, dict) and k in data:
            data = data[k]
        else:
            return None
    return key


def get_raw(dictionary: dict, key: str, default=None):
    """Get a nested key from a dictionary without rendering.

    Args:
        dictionary: The dictionary to get the key from.
        key: The nested key to get, separated by dots.
        default: The default value to return if the key is not found.

    Returns:
        The value of the nested key, or the default value if not found.
    """
    keys = key.split(".")
    data = dictionary
    for k in keys:
        if isinstance(data, dict) and k in data:
            data = data[k]
        else:
            return default
    return data


def is_template(string: str) -> bool:
    """Check if a string is a Jinja2 template.

    Args:
        string: The string to check.

    Returns:
        True if the string is a Jinja2 template, False otherwise.
    """
    if not string:
        return False
    if contains_symbols(string, VARIABLE_START_STRING, VARIABLE_END_STRING):
        return True
    return contains_symbols(string, BLOCK_START_STRING, BLOCK_END_STRING)


def contains_symbols(string: str, open_symbol: str, close_symbol: str) -> bool:
    """Check if a string contains a pair of symbols.

    Args:
        string: The string to check.
        open_symbol: The open symbol.
        close_symbol: The close symbol.

    Returns:
        True if the string contains the pair of symbols, False otherwise.
    """
    open_index = string.find(open_symbol)
    if open_index != -1:
        return string.find(close_symbol, open_index + len(open_symbol)) != -1
    return False
