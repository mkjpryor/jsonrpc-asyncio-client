"""
Tests for the base client implementation.
"""

import asyncio
import json
import uuid

from pydantic import ValidationError

import pytest
import mock

from jsonrpc.model import Request, BatchRequest, Response, JsonRpcException, ParseError

from jsonrpc.client import Client, TransportExhausted
from jsonrpc.client.transport.base import Transport


class AnyUuid:
    """
    Class that compares as equal to any UUID.

    Used when comparing the JSON created by a request - we don't care what the
    id is - only that it is there and is a UUID.
    """
    def __eq__(self, other):
        if isinstance(other, uuid.UUID):
            return True
        # Try to create a UUID object from the string
        try:
            id = uuid.UUID(other)
        except ValueError:
            return False
        else:
            return True


@pytest.fixture
def transport():
    """
    Fixture for a mock transport.
    """
    return mock.AsyncMock(Transport)


@pytest.fixture
def transport_received(transport):
    """
    Fixture for a queue that will receive data sent to the mock transport.
    """
    transport_received = asyncio.Queue()
    async def transport_send(data, ct):
        transport_received.put_nowait((data, ct))
    transport.send.side_effect = transport_send
    return transport_received


@pytest.fixture
def transport_to_send(transport):
    """
    Fixture for a queue that can be used to send data to be yielded by the mock transport.

    If the given data is an exception, it will be raised.
    """
    transport_to_send = asyncio.Queue()
    async def transport_receive():
        while True:
            to_send = await transport_to_send.get()
            if isinstance(to_send, StopAsyncIteration):
                return
            elif isinstance(to_send, Exception):
                raise to_send
            else:
                yield to_send
    transport.receive.side_effect = transport_receive
    return transport_to_send


@pytest.mark.asyncio
async def test_client_send_success(transport, transport_received, transport_to_send):
    """
    Tests that send returns the correct result for a successful request.
    """
    request = Request.create('my_method', 1, 2, 3)
    response_content = json.dumps(dict(jsonrpc = "2.0", id = request.id, result = [4, 5, 6]))
    # Run the test code
    async with Client(transport) as client:
        result_task = asyncio.create_task(client.send(request))
        # Wait for the request data to appear on the receive queue
        await transport_received.get()
        # Then send the response
        transport_to_send.put_nowait(response_content)
        result = await result_task
    # Assert that the data was sent over the transport correctly
    transport.send.assert_awaited_once_with(request.json(), 'application/json')
    # Assert that the transport was closed
    transport.close.assert_awaited_once()
    # Check that the result is correct
    assert result == [4, 5, 6]


@pytest.mark.asyncio
async def test_client_send_notification():
    """
    Tests that send returns the correct result for a notification.
    """
    request = Request.create('my_method', 1, 2, 3, _notification = True)
    transport = mock.AsyncMock(Transport)
    async with Client(transport) as client:
        result = await client.send(request)
    # Assert that the data was sent over the transport correctly
    transport.send.assert_awaited_once_with(request.json(), 'application/json')
    # Assert that the transport was closed
    transport.close.assert_awaited_once()
    assert result is None


@pytest.mark.asyncio
async def test_client_send_error(transport, transport_received, transport_to_send):
    """
    Tests that send raises the correct exception for an error result.
    """
    request = Request.create('my_method', 1, 2, 3)
    response_content = json.dumps(
        dict(
            jsonrpc = "2.0",
            id = request.id,
            error = dict(
                code = -32601,
                message = "Method not found",
                data = "'my_method' does not exist"
            )
        )
    )
    # Check that sending the request raises the correct exception
    with pytest.raises(JsonRpcException) as excinfo:
        async with Client(transport) as client:
            result_task = asyncio.create_task(client.send(request))
            # Wait for the request data to appear on the receive queue
            await transport_received.get()
            # Then send the response
            transport_to_send.put_nowait(response_content)
            result = await result_task
    # Assert that the data was sent over the transport correctly
    transport.send.assert_awaited_once_with(request.json(), 'application/json')
    # Assert that the transport was closed
    transport.close.assert_awaited_once()
    # Check that the exception is correct
    assert excinfo.value.code == -32601
    assert excinfo.value.message == "Method not found"


@pytest.mark.asyncio
async def test_client_send_batch(transport, transport_received, transport_to_send):
    """
    Tests that send returns the correct result for a batch request.
    """
    req1 = Request.create('my_method', 1, 2, 3)
    req2 = Request.create('my_method', 4, 5, 6)
    req3 = Request.create('my_method', 7, 8, 9)
    req4 = Request.create('my_notification', 1, 2, 3, _notification = True)
    request = BatchRequest.create(req1, req2, req3, req4)
    # Return the results out of order - the client should match them up to the requests
    response_content = json.dumps([
        dict(jsonrpc = "2.0", id = req2.id, result = [10, 11, 12]),
        dict(jsonrpc = "2.0", id = req3.id, error = dict(code = -32602, message = "Invalid params")),
        dict(jsonrpc = "2.0", id = req1.id, result = [13, 14, 15])
    ])
    async with Client(transport) as client:
        result_task = asyncio.create_task(client.send(request))
        # Wait for the request data to appear on the receive queue
        await transport_received.get()
        # Then send the response
        transport_to_send.put_nowait(response_content)
        result = await result_task
    # Assert that the data was sent over the transport correctly
    transport.send.assert_awaited_once_with(request.json(), 'application/json')
    # Assert that the transport was closed
    transport.close.assert_awaited_once()
    # Check that the response is the results in the correct order
    assert len(result) == 4
    assert result[0] == [13, 14, 15]
    assert result[1] == [10, 11, 12]
    assert isinstance(result[2], JsonRpcException)
    assert result[2].code == -32602
    assert result[2].message == "Invalid params"
    assert result[3] is None


@pytest.mark.asyncio
async def test_client_out_of_order_resolution(transport, transport_received, transport_to_send):
    """
    Tests that two requests are resolved correctly even if the responses arrive in
    a different order to the way that they are sent.
    """
    request1 = Request.create('my_method', 1, 2, 3)
    request2 = Request.create('my_method', 4, 5, 6)
    response1_content = json.dumps(dict(jsonrpc = "2.0", id = request1.id, result = [7, 8, 9]))
    response2_content = json.dumps(dict(jsonrpc = "2.0", id = request2.id, result = [10, 11, 12]))
    async with Client(transport) as client:
        # Send request1 first
        req1_task = asyncio.create_task(client.send(request1))
        await transport_received.get()
        # Then send request2
        req2_task = asyncio.create_task(client.send(request2))
        await transport_received.get()
        # Then put the responses onto the queue, request2 first
        await transport_to_send.put(response2_content)
        await transport_to_send.put(response1_content)
        # Then wait for both results
        result1, result2 = await asyncio.gather(req1_task, req2_task)
    # Assert that the data was sent over the transport correctly
    transport.send.assert_has_awaits([
        mock.call(request1.json(), 'application/json'),
        mock.call(request2.json(), 'application/json'),
    ])
    # Assert that the transport was closed
    transport.close.assert_awaited_once()
    # Check that the results are correct
    assert result1 == [7, 8, 9]
    assert result2 == [10, 11, 12]


@pytest.mark.asyncio
async def test_client_send_empty_response_data(transport, transport_received, transport_to_send):
    """
    Tests that any empty response data returned by the transport is ignored and
    requests can still be resolved.
    """
    request = Request.create('my_method', 1, 2, 3)
    response_content = json.dumps(dict(jsonrpc = "2.0", id = request.id, result = [4, 5, 6]))
    # Run the test code
    async with Client(transport) as client:
        result_task = asyncio.create_task(client.send(request))
        # Wait for the request data to appear on the receive queue
        await transport_received.get()
        # Send an empty response followed by the actual response
        transport_to_send.put_nowait('')
        transport_to_send.put_nowait(response_content)
        result = await result_task
    # Assert that the data was sent over the transport correctly
    transport.send.assert_awaited_once_with(request.json(), 'application/json')
    # Assert that the transport was closed
    transport.close.assert_awaited_once()
    # Check that the result is correct
    assert result == [4, 5, 6]


@pytest.mark.asyncio
async def test_client_send_unassociated_response(transport, transport_received, transport_to_send):
    """
    Tests that a valid response that is not associated with a known request is ignored
    and doesn't prevent other requests from resolving.
    """
    request = Request.create('my_method', 1, 2, 3)
    invalid_response_content = json.dumps(dict(jsonrpc = "2.0", id = 10, result = "not associated"))
    valid_response_content = json.dumps(dict(jsonrpc = "2.0", id = request.id, result = [4, 5, 6]))
    # Run the test code
    async with Client(transport) as client:
        result_task = asyncio.create_task(client.send(request))
        # Wait for the request data to appear on the receive queue
        await transport_received.get()
        # Send the unassocaited response followed by the actual response
        transport_to_send.put_nowait(invalid_response_content)
        transport_to_send.put_nowait(valid_response_content)
        result = await result_task
    # Assert that the data was sent over the transport correctly
    transport.send.assert_awaited_once_with(request.json(), 'application/json')
    # Assert that the transport was closed
    transport.close.assert_awaited_once()
    # Check that the result is correct
    assert result == [4, 5, 6]


@pytest.mark.asyncio
async def test_client_send_response_not_valid_json(transport, transport_received, transport_to_send):
    """
    Tests that a response that is not valid JSON causes a warning to be emitted, but does not
    prevent requests from resolving in the future.
    """
    request = Request.create('my_method', 1, 2, 3)
    invalid_response_content = '{"jsonrpc": "2.0", "id": 10'  # Not well-formed JSON
    valid_response_content = json.dumps(dict(jsonrpc = "2.0", id = request.id, result = [4, 5, 6]))
    # Run the test code
    with pytest.warns(RuntimeWarning) as warnings:
        async with Client(transport) as client:
            result_task = asyncio.create_task(client.send(request))
            # Wait for the request data to appear on the receive queue
            await transport_received.get()
            # Send the unassocaited response followed by the actual response
            transport_to_send.put_nowait(invalid_response_content)
            transport_to_send.put_nowait(valid_response_content)
            result = await result_task
    # Assert that the data was sent over the transport correctly
    transport.send.assert_awaited_once_with(request.json(), 'application/json')
    # Assert that the transport was closed
    transport.close.assert_awaited_once()
    # Assert that the correct warning was raised
    assert len(warnings) == 1
    assert warnings[0].message.args[0] == 'received invalid JSON'
    # Assert that the request still completed successfully
    assert result == [4, 5, 6]


@pytest.mark.asyncio
async def test_client_send_response_not_valid_response(transport, transport_received, transport_to_send):
    """
    Tests that a response that is not a valid JSON-RPC response causes a warning to be
    emitted, but does not prevent requests from resolving in the future.
    """
    request = Request.create('my_method', 1, 2, 3)
    invalid_response_content = '{"jsonrpc": "2.0", "id": 10}'  # Well-formed JSON but result or error is required
    valid_response_content = json.dumps(dict(jsonrpc = "2.0", id = request.id, result = [4, 5, 6]))
    # Run the test code
    with pytest.warns(RuntimeWarning) as warnings:
        async with Client(transport) as client:
            result_task = asyncio.create_task(client.send(request))
            # Wait for the request data to appear on the receive queue
            await transport_received.get()
            # Send the unassocaited response followed by the actual response
            transport_to_send.put_nowait(invalid_response_content)
            transport_to_send.put_nowait(valid_response_content)
            result = await result_task
    # Assert that the data was sent over the transport correctly
    transport.send.assert_awaited_once_with(request.json(), 'application/json')
    # Assert that the transport was closed
    transport.close.assert_awaited_once()
    # Assert that the correct warning was raised
    assert len(warnings) == 1
    assert warnings[0].message.args[0] == 'received invalid JSON-RPC response'
    # Assert that the request still completed successfully
    assert result == [4, 5, 6]


@pytest.mark.asyncio
async def test_client_send_error_response_id_null(transport, transport_received, transport_to_send):
    """
    Tests that a JSON-RPC response without an id, e.g. a parse error, causes a warning to be
    emitted, but does not prevent requests from resolving in the future.
    """
    request = Request.create('my_method', 1, 2, 3)
    invalid_response_content = Response.create_error(ParseError('invalid JSON')).json()
    valid_response_content = json.dumps(dict(jsonrpc = "2.0", id = request.id, result = [4, 5, 6]))
    # Run the test code
    with pytest.warns(RuntimeWarning) as warnings:
        async with Client(transport) as client:
            result_task = asyncio.create_task(client.send(request))
            # Wait for the request data to appear on the receive queue
            await transport_received.get()
            # Send the unassocaited response followed by the actual response
            transport_to_send.put_nowait(invalid_response_content)
            transport_to_send.put_nowait(valid_response_content)
            result = await result_task
    # Assert that the data was sent over the transport correctly
    transport.send.assert_awaited_once_with(request.json(), 'application/json')
    # Assert that the transport was closed
    transport.close.assert_awaited_once()
    # Assert that the correct warning was raised
    assert len(warnings) == 1
    assert warnings[0].message.args[0] == "JsonRpcException('Parse error', code = -32700)"
    # Assert that the request still completed successfully
    assert result == [4, 5, 6]


@pytest.mark.asyncio
async def test_client_transport_exhausted(transport, transport_received, transport_to_send):
    """
    Tests that a request is resolved with a suitable exception if the transport is
    exhausted before the request is resolved.
    """
    request = Request.create('my_method', 1, 2, 3)
    with pytest.raises(TransportExhausted) as excinfo:
        async with Client(transport) as client:
            result_task = asyncio.create_task(client.send(request))
            # Wait for the request data to appear on the receive queue
            await transport_received.get()
            # Make the transport receive async iterator exit without error
            transport_to_send.put_nowait(StopAsyncIteration())
            result = await result_task
    # Assert that the data was sent over the transport correctly
    transport.send.assert_awaited_once_with(request.json(), 'application/json')
    # Assert that the transport was closed
    transport.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_client_transport_error(transport, transport_received, transport_to_send):
    """
    Tests that the request is resolved with an exception if the transport exits
    with an error.
    """
    request = Request.create('my_method', 1, 2, 3)
    with pytest.raises(ValueError) as excinfo:
        async with Client(transport) as client:
            result_task = asyncio.create_task(client.send(request))
            # Wait for the request data to appear on the receive queue
            await transport_received.get()
            # Make the transport receive async iterator exit without error
            transport_to_send.put_nowait(ValueError('some error happened'))
            result = await result_task
    # Assert that the data was sent over the transport correctly
    transport.send.assert_awaited_once_with(request.json(), 'application/json')
    # Assert that the transport was closed
    transport.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_client_call_success(transport, transport_received, transport_to_send):
    """
    Tests that calling a method returns the correct result for a successful invocation.
    """
    # Run the test code
    async with Client(transport) as client:
        result_task = asyncio.create_task(client.call('my_method', 1, 2, 3))
        # Wait for the request data to appear on the receive queue
        request_data, _ = await transport_received.get()
        # Then send the response
        request_id = json.loads(request_data)['id']
        response_content = json.dumps(dict(jsonrpc = "2.0", id = request_id, result = [4, 5, 6]))
        transport_to_send.put_nowait(response_content)
        result = await result_task
    # Assert that the data was sent over the transport correctly
    transport.send.assert_awaited_once()
    request = json.loads(transport.send.await_args.args[0])
    expected = dict(jsonrpc = "2.0", id = AnyUuid(), method = 'my_method', params = [1, 2, 3])
    assert request == expected
    # Assert that the transport was closed
    transport.close.assert_awaited_once()
    # Check that the result is correct
    assert result == [4, 5, 6]


@pytest.mark.asyncio
async def test_client_notify():
    """
    Tests that notifying a method does the correct thing.
    """
    transport = mock.AsyncMock(Transport)
    async with Client(transport) as client:
        result = await client.notify('my_method', 1, 2, 3)
    # Assert that the data was sent over the transport correctly
    transport.send.assert_awaited_once()
    request = json.loads(transport.send.await_args.args[0])
    expected = dict(jsonrpc = "2.0", method = 'my_method', params = [1, 2, 3])
    assert request == expected
    # Assert that the transport was closed
    transport.close.assert_awaited_once()
    assert result is None


@pytest.mark.asyncio
async def test_client_call_error(transport, transport_received, transport_to_send):
    """
    Tests that calling a method in error raises the correct exception.
    """
    # Run the test code
    with pytest.raises(JsonRpcException) as excinfo:
        async with Client(transport) as client:
            result_task = asyncio.create_task(client.call('my_method', 1, 2, 3))
            # Wait for the request data to appear on the receive queue
            request_data, _ = await transport_received.get()
            # Then send the response
            request_id = json.loads(request_data)['id']
            response_content = json.dumps(
                dict(
                    jsonrpc = "2.0",
                    id = request_id,
                    error = dict(
                        code = -32601,
                        message = "Method not found",
                        data = "'my_method' does not exist"
                    )
                )
            )
            transport_to_send.put_nowait(response_content)
            result = await result_task
    # Assert that the data was sent over the transport correctly
    transport.send.assert_awaited_once()
    request = json.loads(transport.send.await_args.args[0])
    expected = dict(jsonrpc = "2.0", id = AnyUuid(), method = 'my_method', params = [1, 2, 3])
    assert request == expected
    # Assert that the transport was closed
    transport.close.assert_awaited_once()
    # Check that sending the request raises the correct exception
    assert excinfo.value.code == -32601
    assert excinfo.value.message == "Method not found"


@pytest.mark.asyncio
async def test_client_batch(transport, transport_received, transport_to_send):
    """
    Tests that awaiting a bound request returns the correct result for a batch request.
    """
    req1 = Request.create('my_method', 1, 2, 3)
    req2 = Request.create('my_method', 4, 5, 6)
    req3 = Request.create('my_method', 7, 8, 9)
    req4 = Request.create('my_notification', 1, 2, 3, _notification = True)
    # Return the results out of order - the client should match them up to the requests
    response_content = json.dumps([
        dict(jsonrpc = "2.0", id = req2.id, result = [10, 11, 12]),
        dict(jsonrpc = "2.0", id = req3.id, error = dict(code = -32602, message = "Invalid params")),
        dict(jsonrpc = "2.0", id = req1.id, result = [13, 14, 15])
    ])
    async with Client(transport) as client:
        result_task = asyncio.create_task(client.batch(req1, req2, req3, req4))
        # Wait for the request data to appear on the receive queue
        await transport_received.get()
        # Then send the response
        transport_to_send.put_nowait(response_content)
        result = await result_task
    # Assert that the data was sent over the transport correctly
    transport.send.assert_awaited_once()
    request = json.loads(transport.send.await_args.args[0])
    expected = [
        dict(jsonrpc = "2.0", id = req1.id, method = 'my_method', params = [1, 2, 3]),
        dict(jsonrpc = "2.0", id = req2.id, method = 'my_method', params = [4, 5, 6]),
        dict(jsonrpc = "2.0", id = req3.id, method = 'my_method', params = [7, 8, 9]),
        dict(jsonrpc = "2.0", method = 'my_notification', params = [1, 2, 3])
    ]
    assert request == expected
    # Assert that the transport was closed
    transport.close.assert_awaited_once()
    # Check that the response is the results in the correct order
    assert len(result) == 4
    assert result[0] == [13, 14, 15]
    assert result[1] == [10, 11, 12]
    assert isinstance(result[2], JsonRpcException)
    assert result[2].code == -32602
    assert result[2].message == "Invalid params"
    assert result[3] is None


@pytest.mark.asyncio
async def test_client_magic_method(transport, transport_received, transport_to_send):
    """
    Tests that calling a remote method using a client attribute produces the correct result.
    """
    # Run the test code
    async with Client(transport) as client:
        result_task = asyncio.create_task(client.my_method(1, 2, 3))
        # Wait for the request data to appear on the receive queue
        request_data, _ = await transport_received.get()
        # Then send the response
        request_id = json.loads(request_data)['id']
        response_content = json.dumps(dict(jsonrpc = "2.0", id = request_id, result = [4, 5, 6]))
        transport_to_send.put_nowait(response_content)
        result = await result_task
    # Assert that the data was sent over the transport correctly
    transport.send.assert_awaited_once()
    request = json.loads(transport.send.await_args.args[0])
    expected = dict(jsonrpc = "2.0", id = AnyUuid(), method = 'my_method', params = [1, 2, 3])
    assert request == expected
    # Assert that the transport was closed
    transport.close.assert_awaited_once()
    # Check that the result is correct
    assert result == [4, 5, 6]
