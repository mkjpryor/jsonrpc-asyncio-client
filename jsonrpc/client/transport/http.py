"""
Module containing the HTTP transport for JSON-RPC clients.
"""

import asyncio

import httpx

from .base import RequestResponseTransport


class Transport(RequestResponseTransport):
    """
    HTTP transport for a JSON-RPC client.
    """
    def __init__(self, endpoint, **client_kwargs):
        super().__init__()
        self.endpoint = endpoint
        self.client = httpx.AsyncClient(**client_kwargs)

    async def request(self, data, content_type):
        response = await self.client.post(
            self.endpoint,
            data = data,
            # Make sure to insert the content type header
            headers = {'content-type': content_type}
        )
        response.raise_for_status()
        return response.text

    async def close(self):
        await self.client.aclose()
