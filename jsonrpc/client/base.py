"""
Module containing the base implementation for JSON-RPC clients.
"""

import asyncio
import functools
import json
import uuid

from .exceptions import InvalidRequest, MethodExecutionError


class Request:
    """
    Represents a JSON-RPC request.
    """
    def __init__(self, method, *args, _notification = False, **kwargs):
        # The params can be *either* positional or keyword
        if args and kwargs:
            raise InvalidRequest('Positional and keyword arguments are mutually exclusive.')
        # Unless we are a notification, generate an id
        self.id = str(uuid.uuid4()) if not _notification else None
        self.method = method
        self.params = args or kwargs

    @property
    def is_notification(self):
        """
        Indicates if the request is a notification.
        """
        return not self.id

    def as_dict(self):
        """
        Convert this request to a dict.
        """
        result = dict(jsonrpc = "2.0", method = self.method)
        if self.params:
            result.update(params = self.params)
        if self.id:
            result.update(id = self.id)
        return result

    def as_json(self, **kwargs):
        """
        Return a JSON representation of this request.
        """
        return json.dumps(self.as_dict(), **kwargs)


class Notification(Request):
    """
    Represents a JSON-RPC notification.
    """
    def __init__(self, method, *args, **kwargs):
        super().__init__(method, *args, _notification = True, **kwargs)


class BatchRequest:
    """
    Represents a JSON-RPC batch request.
    """
    def __init__(self, *requests):
        # The batch must have at least one request
        if not requests:
            raise InvalidRequest('Batch must have at least one request.')
        self.requests = tuple(requests)

    def __len__(self):
        return len(self.requests)

    def __iter__(self):
        return iter(self.requests)

    def as_list(self):
        """
        Convert this batch request to a list of request dictionaries.
        """
        return [r.as_dict() for r in self.requests]

    def as_json(self, **kwargs):
        """
        Return a JSON representation of this batch request.
        """
        return json.dumps(self.as_list(), **kwargs)


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
        return (await self.send(Request(method, *args, **kwargs)))

    async def notify(self, method, *args, **kwargs):
        """
        Notifies the given JSON-RPC method with the given arguments.
        """
        return (await self.send(Notification(method, *args, **kwargs)))

    async def batch(self, *requests):
        """
        Sends a batch of JSON-RPC requests and returns a list of the results in the
        same order as the requests.
        """
        return (await self.send(BatchRequest(*requests)))

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
        """
        async for response_data in self.transport.receive():
            if not response_data:
                return
            # Get a list of responses from the response data
            responses = json.loads(response_data)
            if not isinstance(responses, list):
                responses = [responses]
            # Process each response to get either a result or error
            for response in responses:
                # If the response doesn't have an id, there is nothing to do
                response_id = response.get('id')
                if not response_id:
                    return
                # Get the corresponding future
                try:
                    future = self._futures.pop(response_id)
                except KeyError:
                    continue
                # Resolve the future either with a result or an exception
                if 'error' in response:
                    error = MethodExecutionError(
                        code = response['error']['code'],
                        message = response['error']['message'],
                        data = response['error'].get('data')
                    )
                    future.set_exception(error)
                else:
                    future.set_result(response.get('result'))

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
        request_data = request.as_json(cls = self.json_encoder)
        await self.transport.send(request_data, 'application/json')
        # Wait for the futures to be resolved
        return await result

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
