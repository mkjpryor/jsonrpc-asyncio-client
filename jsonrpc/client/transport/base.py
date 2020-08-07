"""
Module containing base class for transports.
"""

import abc
import asyncio
import json


class Transport(abc.ABC):
    """
    Base class for a transport.

    Transports are async context managers, so can be used in async with statements
    to ensure that any open connections are closed.
    """
    @abc.abstractmethod
    async def send(self, data, content_type):
        """
        Send the given data over the transport with the given content type.
        """

    @abc.abstractmethod
    def receive(self):
        """
        Return an async iterator for receiving data over the transport.
        """

    async def close(self):
        """
        Close the transport.
        """

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.close()


class RequestResponseTransport(Transport):
    """
    Base class for transports whose underlying protocol is request-response, e.g. HTTP.
    """
    def __init__(self):
        # Each call to send pushes items onto the queue, which are then yielded from receive
        self.queue = asyncio.Queue()

    @abc.abstractmethod
    async def request(self, data, content_type):
        """
        Make a request with the given data and content type and return the result.
        """

    async def send(self, data, content_type):
        # Make the request and get the response
        response_data = await self.request(data, content_type)
        # If the response is non-empty, push it onto the queue
        if response_data:
            self.queue.put_nowait(response_data)

    async def receive(self):
        # Yield items from the queue as they become available
        while True:
            response_data = await self.queue.get()
            yield response_data
