from fnmatch import fnmatch

import httpx
from sanic.log import logger

from app.models.network import HTTPProxy, ProxyProtocol, URLRule
from app.utils.crypto import xor_decrypt


class NetworkTransport(httpx.AsyncHTTPTransport):
    """An HTTP transport that routes requests via custom DNS and proxy rules."""

    def __init__(self, **kwargs):
        self._transport_kwargs = kwargs
        self._proxy_transports: dict[str, httpx.AsyncHTTPTransport] = {}
        super().__init__(**kwargs)

    async def aclose(self) -> None:
        for transport in self._proxy_transports.values():
            await transport.aclose()
        self._proxy_transports.clear()
        await super().aclose()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        logger.debug("Handling request for URL: %s", url)

        proxy_rules = (
            await URLRule.filter(http_proxy=True, proxy_id__not_isnull=True)
            .select_related("proxy")
            .order_by("priority")
        )
        for rule in proxy_rules:
            pattern = rule.pattern
            pattern = pattern if pattern.endswith("*") else pattern + "*"
            if fnmatch(url, pattern) and (proxy := rule.proxy):
                logger.debug(
                    "URL matches pattern '%s', routing via proxy '%s'",
                    pattern,
                    proxy.name,
                )
                return await self._proxy_async_request(proxy, request)

        # use the default transport if no proxy rules match
        return await super().handle_async_request(request)

    async def _proxy_async_request(
        self, proxy: HTTPProxy, request: httpx.Request
    ) -> httpx.Response:
        """Make an asynchronous HTTP request via the specified proxy.

        Args:
            proxy: The HTTP proxy to use for the request.
            request: The original request that needs to be proxied.

        Returns:
            The response from the proxied request.
        """

        # construct the proxy URL with authentication if needed
        scheme = "socks5" if proxy.protocol == ProxyProtocol.SOCKS5 else "http"
        if (username := proxy.username) and (password := proxy.password):
            password = xor_decrypt(password)
            proxy_url = f"{scheme}://{username}:{password}@{proxy.host}:{proxy.port}"
        else:
            proxy_url = f"{scheme}://{proxy.host}:{proxy.port}"

        # create or reuse the transport for the proxy
        transport = self._proxy_transports.get(proxy_url)
        if transport is None:
            transport = httpx.AsyncHTTPTransport(
                proxy=proxy_url, **self._transport_kwargs
            )
            self._proxy_transports[proxy_url] = transport

        # send the request via the proxy transport
        return await transport.handle_async_request(request)
