"""MCP SDK transport loaded only when an actual MCP connection is requested."""
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.client.sse import sse_client

from .common import Refused, require
from .stdio_transport import confined_stdio


class BoundedResponse(httpx2.AsyncByteStream):
    def __init__(self, stream):
        self.stream = stream

    async def __aiter__(self):
        size = 0
        async for chunk in self.stream:
            size += len(chunk)
            require(size <= 2000000, 'MCP HTTP response exceeds the transport bound')
            yield chunk

    async def aclose(self):
        await self.stream.aclose()


def http_client(headers, endpoint=None):
    async def guard_request(request):
        if endpoint:
            selected, actual = urlsplit(endpoint), urlsplit(str(request.url))
            require((actual.scheme, actual.hostname, actual.port or (443 if actual.scheme == 'https' else 80)) ==
                    (selected.scheme, selected.hostname, selected.port or (443 if selected.scheme == 'https' else 80)),
                    'MCP request escaped the configured origin')

    async def reject_redirect(response):
        require(not response.is_redirect, 'MCP redirect refused; uncertain writes must not be resent')
        require(response.headers.get('content-encoding', 'identity').lower() in {'', 'identity'},
                'Compressed MCP responses are unavailable under the memory bound')
        response.stream = BoundedResponse(response.stream)

    headers = {k: v for k, v in headers.items() if k.lower() != 'accept-encoding'}
    return httpx2.AsyncClient(headers={**headers, 'Accept-Encoding': 'identity'}, follow_redirects=False,
                             trust_env=False, timeout=40,
                             event_hooks={'request': [guard_request], 'response': [reject_redirect]})


@asynccontextmanager
async def client(config, root, audit):
    transport = config.get('transport', 'stdio' if 'command' in config else 'streamable-http')
    if transport == 'stdio':
        async with Client(confined_stdio(config, root, audit=audit), read_timeout_seconds=40,
                          input_required_max_rounds=0, cache=None) as connection:
            yield connection
    elif transport == 'streamable-http':
        async with http_client(config.get('headers', {}), config['url']) as http:
            async with Client(streamable_http_client(config['url'], http_client=http), read_timeout_seconds=40,
                              input_required_max_rounds=0, cache=None) as connection:
                yield connection
    elif transport == 'sse':
        # Protocol negotiation and cancellation belong to the SDK. No server
        # sampling or elicitation callback grants a peer operator authority.
        async with Client(sse_client(config['url'], headers=config.get('headers', {}), sse_read_timeout=40,
                          httpx_client_factory=lambda headers=None, **kwargs: http_client(headers or {}, config['url'])),
                          read_timeout_seconds=40, input_required_max_rounds=0, cache=None) as connection:
            yield connection
    else:
        raise Refused('Choose stdio, streamable-http or sse')
