"""Remote resource proxy utilities."""

from collections.abc import Mapping
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from pydantic import BaseModel, Field

from app.core.exceptions import ForbiddenException

SENSITIVE_REQUEST_HEADERS = {
    "authorization",
    "cookie",
    "proxy",
    "proxy-authorization",
}

PROXY_RESPONSE_HEADERS = [
    "accept-ranges",
    "cache-control",
    "content-encoding",
    "content-disposition",
    "content-length",
    "content-range",
    "content-type",
    "etag",
    "expires",
    "last-modified",
]


class RemoteProxy(BaseModel):
    """Request model for proxying a remote resource."""

    url: str = Field(min_length=1)
    store: bool = False
    referer: str | None = None
    ua: str | None = None


def remote_proxy_request(
    url: str,
    referer: str | None,
    user_agent: str | None = None,
    request_headers: Mapping[str, str] | None = None,
) -> tuple[str, dict[str, str]]:
    """Build the upstream URL and headers for a remote proxy request.

    Args:
        url: The user-provided remote resource URL.
        referer: Optional referer override for the upstream request.
        user_agent: Optional user agent override for the upstream request.
        request_headers: Incoming request headers to selectively forward.

    Returns:
        The sanitized upstream URL and request headers.

    Raises:
        ForbiddenException: If the URL is not an absolute HTTP(S) URL.
    """
    parsed = urlparse(url)
    query_params: list[tuple[str, str]] = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        # strip app-only proxy params before forwarding the upstream request
        if key == "proxy":
            continue
        if key == "referer":
            referer = referer or value or None
            continue
        query_params.append((key, value))

    url = urlunparse(parsed._replace(query=urlencode(query_params)))
    parsed = urlparse(url)
    # only proxy absolute http urls to avoid local file or internal path access
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ForbiddenException

    # forward browser playback headers while dropping credentials and proxy-only headers
    dropped = SENSITIVE_REQUEST_HEADERS | {"host", "referer"}
    if user_agent:
        dropped |= {"user-agent"}
    headers = {
        str(key): str(value)
        for key, value in (request_headers or {}).items()
        if str(key).lower() not in dropped
    }
    headers["Host"] = parsed.netloc
    headers["Referer"] = referer or url
    if user_agent:
        headers["User-Agent"] = user_agent
    return url, headers
