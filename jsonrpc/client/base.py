"""
Module containing the JSON-RPC client.
"""

import asyncio
import functools
import json
import warnings

from pydantic import ValidationError

from jsonrpc.model import Request, BatchRequest, Response

from .exceptions import TransportExhausted


class Client:
    """
    Class for a JSON-RPC client.
    """
    def __init__(self, transport, json_encoder = None):
        self.json_encoder = json_encoder
        self.transport = transport
        # Keep a dict of futures for responses indexed by request id
        self._futures = {}
        # Schedule a task to listen for messages from the transport
        self._listen_task = asyncio.create_task(self._listen())

    async def call(self, method, *args, **kwargs):
        """
        Calls the given JSON-RPC method with the given arguments and returns the result.
        """
        return (await self.send(Request.create(method, *args, **kwargs)))

    async def notify(self, method, *args, **kwargs):
        """
        Notifies the given JSON-RPC method with the given arguments.
        """
        return (await self.send(Request.create(method, *args, _notification = True, **kwargs)))

    async def batch(self, *requests):
        """
        Sends a batch of JSON-RPC requests and returns a list of the results in the
        same order as the requests.
        """
        return (await self.send(BatchRequest.create(*requests)))

    def __getattr__(self, name):
        """
        Treat any missing attributes as JSON-RPC method calls.
        """
        return functools.partial(self.call, name)

    def _make_future(self, request):
        """
        Return a future representing the future result for the given request.
        """
        future = asyncio.get_running_loop().create_future()
        # If the request has an id, save the future to be resolved later
        # If not, the request is a notification so resolve the future now
        if request.id:
            self._futures[request.id] = future
        else:
            future.set_result(None)
        return future

    async def _listen(self):
        """
        Listen for responses from the transport and handle them as appropriate.

        Error conditions that cannot be associated with a request, such as badly-formed
        JSON or invalid responses, are emitted as warnings.

        This is because we do not want to allow these conditions to raise as this would
        cause the listen task to exit and render the client unable to process future requests.

        Python can be configured to raise on warnings if this is the desired behaviour, i.e.
        if the client is sending one request at a time.
        """
        async for response_data in self.transport.receive():
            if not response_data:
                continue
            try:
                responses = json.loads(response_data)
            except json.decoder.JSONDecodeError:
                warnings.warn('received invalid JSON', RuntimeWarning)
                continue
            if not isinstance(responses, list):
                responses = [responses]
            # Process each response to get either a result or error
            for response_obj in responses:
                # Check if the received object is a valid response
                try:
                    response = Response.parse_obj(response_obj)
                except ValidationError:
                    warnings.warn('received invalid JSON-RPC response', RuntimeWarning)
                    continue
                # If the response doesn't have an id, emit a warning
                # Note that it must be an error because the response model prevents a response
                # from being created with a result and a null id, as this would be a
                # notification and hence no response should be produced
                if not response.id:
                    warnings.warn(repr(response.error.exception()), RuntimeWarning)
                    continue
                # Get the corresponding future
                try:
                    future = self._futures.pop(response.id)
                except KeyError:
                    continue
                # Resolve the future either with a result or an exception
                if response.error:
                    future.set_exception(response.error.exception())
                else:
                    future.set_result(response.result)

    async def send(self, request):
        """
        Send the given request or batch request and return the result.

        For individual requests, the result is returned and any errors are raised.
        For batch requests, a list of results are returned in the same order as the
        requests. In the case of an error, the error is returned in place of the
        result.
        """
        if isinstance(request, Request):
            result = self._make_future(request)
        else:
            # For a batch request, make a future for each request in the batch
            # The result resolves when all the futures have resolved
            futures = [self._make_future(r) for r in request]
            result = asyncio.gather(*futures, return_exceptions = True)
        # Serialize the request using JSON and send it
        request_data = request.json(cls = self.json_encoder)
        await self.transport.send(request_data, 'application/json')
        # Wait for the futures to be resolved
        # If the listen task exits, we also want to stop waiting for the result
        done, pending = await asyncio.wait(
            [result, self._listen_task],
            return_when = asyncio.FIRST_COMPLETED
        )
        if result in done:
            # If the result future is in the completed tasks, get the result
            # If it resolved with an exception, this will raise
            return result.result()
        else:
            # If the task that completed was the listen, we want to raise an error
            # This can be either the error that the listen exited with or transport exhausted
            # if the async iterator exited without an error
            self._listen_task.result()
            raise TransportExhausted()

    async def close(self):
        """
        Close the client.
        """
        # Stop listening for responses, then close the transport
        self._listen_task.cancel()
        # Just close the underlying transport
        await self.transport.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.close()
