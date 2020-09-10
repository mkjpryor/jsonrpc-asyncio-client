"""
Module containing the HTTP transport for JSON-RPC clients.
"""

import asyncio
import itertools
import json

import websockets

from .base import Transport as BaseTransport


class Transport(BaseTransport):
    """
    Websocket transport for a JSON-RPC client.
    """
    # Use a 16MB max message size by default, the same as hypercorn
    # The default is 1MB
    WEBSOCKET_MAX_SIZE_BYTES = 16 * 1024 * 1024

    def __init__(self, endpoint, **connect_kwargs):
        super().__init__()
        self.connect = websockets.connect(
            endpoint,
            max_size = self.WEBSOCKET_MAX_SIZE_BYTES,
            **connect_kwargs
        )
        async def _client():
            return await self.connect
        self.client = asyncio.create_task(_client())

    async def send(self, data, content_type):
        client = await self.client
        await client.send(data)

    async def receive(self):
        client = await self.client
        while True:
            response_data = await client.recv()
            yield response_data

    async def close(self):
        client = await self.client
        await client.close()
