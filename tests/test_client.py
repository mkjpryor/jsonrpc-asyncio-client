"""
Tests for the base client implementation.
"""

import asyncio
import json
import uuid

import pytest
import mock

from jsonrpc.client.base import Request, Notification, BatchRequest, Client
from jsonrpc.client.transport.base import Transport
from jsonrpc.client.exceptions import InvalidRequest, MethodExecutionError


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


def test_request_positional_args():
    """
    Tests that the correct dict is produced for a request with positional args.
    """
    request = Request('my_method', 1, '2', [3, 4], {'5': 6, '7': 8})
    assert not request.is_notification
    expected = dict(
        jsonrpc = "2.0",
        id = AnyUuid(),
        method = 'my_method',
        params = (1, '2', [3, 4], {'5': 6, '7': 8})
    )
    assert request.as_dict() == expected


def test_request_keyword_args():
    """
    Tests that the correct dict is produced for a request with keyword args.
    """
    request = Request('my_method', arg1 = 1, arg2 = '2', arg3 = [3, 4], arg4 = {'5': 6, '7': 8})
    assert not request.is_notification
    expected = dict(
        jsonrpc = "2.0",
        id = AnyUuid(),
        method = 'my_method',
        params = dict(arg1 = 1, arg2 = '2', arg3 = [3, 4], arg4 = {'5': 6, '7': 8})
    )
    assert request.as_dict() == expected


def test_request_no_params():
    """
    Tests that the correct dict is produced for a request with no parameters.
    """
    request = Request('my_method')
    assert not request.is_notification
    assert request.as_dict() == dict(jsonrpc = "2.0", id = AnyUuid(), method = 'my_method')


def test_request_invalid_params():
    """
    Tests that an error is raised when invalid parameters are given to a request.
    """
    with pytest.raises(InvalidRequest):
        request = Request('my_method', 1, 2, arg1 = 3)


def test_request_notification():
    """
    Tests that the correct dict is produced for a notification.
    """
    request = Notification('my_method', 1, 2, 3)
    assert request.is_notification
    assert request.as_dict() == dict(jsonrpc = "2.0", method = 'my_method', params = (1, 2, 3))


def test_batch_request():
    """
    Tests that the correct list is produced for a batch request.
    """
    req1 = Request('my_method', 1, 2, 3)
    req2 = Request('my_other_method', arg1 = 1, arg2 = 2)
    req3 = Notification('my_notification', 1, 2, 3)
    request = BatchRequest(req1, req2, req3)
    # Check the support for length tests
    assert len(request) == 3
    # Check the support for iterating a request
    assert list(request) == [req1, req2, req3]
    assert request.as_list() == [
        dict(jsonrpc = "2.0", id = AnyUuid(), method = 'my_method', params = (1, 2, 3)),
        dict(
            jsonrpc = "2.0",
            id = AnyUuid(),
            method = 'my_other_method',
            params = dict(arg1 = 1, arg2 = 2)
        ),
        dict(jsonrpc = "2.0", method = "my_notification", params = (1, 2, 3)),
    ]


def test_batch_request_empty():
    """
    Tests that an error is raised when an empty batch request is created.
    """
    with pytest.raises(InvalidRequest):
        request = BatchRequest()


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
    """
    transport_to_send = asyncio.Queue()
    async def transport_receive():
        while True:
            yield await transport_to_send.get()
    transport.receive.side_effect = transport_receive
    return transport_to_send


@pytest.mark.asyncio
async def test_send_success(transport, transport_received, transport_to_send):
    """
    Tests that send returns the correct result for a successful request.
    """
    request = Request('my_method', 1, 2, 3)
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
    transport.send.assert_awaited_once_with(request.as_json(), 'application/json')
    # Assert that the transport was closed
    transport.close.assert_awaited_once()
    # Check that the result is correct
    assert result == [4, 5, 6]


@pytest.mark.asyncio
async def test_send_notification():
    """
    Tests that send returns the correct result for a notification.
    """
    request = Notification('my_method', 1, 2, 3)
    transport = mock.AsyncMock(Transport)
    async with Client(transport) as client:
        result = await client.send(request)
    # Assert that the data was sent over the transport correctly
    transport.send.assert_awaited_once_with(request.as_json(), 'application/json')
    # Assert that the transport was closed
    transport.close.assert_awaited_once()
    assert result is None


@pytest.mark.asyncio
async def test_send_error(transport, transport_received, transport_to_send):
    """
    Tests that send raises the correct exception for an error result.
    """
    request = Request('my_method', 1, 2, 3)
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
    with pytest.raises(MethodExecutionError) as excinfo:
        async with Client(transport) as client:
            result_task = asyncio.create_task(client.send(request))
            # Wait for the request data to appear on the receive queue
            await transport_received.get()
            # Then send the response
            transport_to_send.put_nowait(response_content)
            result = await result_task
    # Assert that the data was sent over the transport correctly
    transport.send.assert_awaited_once_with(request.as_json(), 'application/json')
    # Assert that the transport was closed
    transport.close.assert_awaited_once()
    # Check that the exception is correct
    assert excinfo.value.code == -32601
    assert excinfo.value.message == "Method not found"


@pytest.mark.asyncio
async def test_send_batch(transport, transport_received, transport_to_send):
    """
    Tests that send returns the correct result for a batch request.
    """
    req1 = Request('my_method', 1, 2, 3)
    req2 = Request('my_method', 4, 5, 6)
    req3 = Request('my_method', 7, 8, 9)
    req4 = Notification('my_notification', 1, 2, 3)
    request = BatchRequest(req1, req2, req3, req4)
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
    transport.send.assert_awaited_once_with(request.as_json(), 'application/json')
    # Assert that the transport was closed
    transport.close.assert_awaited_once()
    # Check that the response is the results in the correct order
    assert len(result) == 4
    assert result[0] == [13, 14, 15]
    assert result[1] == [10, 11, 12]
    assert isinstance(result[2], MethodExecutionError)
    assert result[2].code == -32602
    assert result[2].message == "Invalid params"
    assert result[3] is None


@pytest.mark.asyncio
async def test_out_of_order_resolution(transport, transport_received, transport_to_send):
    """
    Tests that two requests are resolved correctly even if the responses arrive in
    a different order to the way that they are sent.
    """
    request1 = Request('my_method', 1, 2, 3)
    request2 = Request('my_method', 4, 5, 6)
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
        mock.call(request1.as_json(), 'application/json'),
        mock.call(request2.as_json(), 'application/json'),
    ])
    # Assert that the transport was closed
    transport.close.assert_awaited_once()
    # Check that the results are correct
    assert result1 == [7, 8, 9]
    assert result2 == [10, 11, 12]


@pytest.mark.asyncio
async def test_call_success(transport, transport_received, transport_to_send):
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
async def test_call_notify():
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
async def test_call_error(transport, transport_received, transport_to_send):
    """
    Tests that calling a method in error raises the correct exception.
    """
    # Run the test code
    with pytest.raises(MethodExecutionError) as excinfo:
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
async def test_call_batch(transport, transport_received, transport_to_send):
    """
    Tests that awaiting a bound request returns the correct result for a batch request.
    """
    req1 = Request('my_method', 1, 2, 3)
    req2 = Request('my_method', 4, 5, 6)
    req3 = Request('my_method', 7, 8, 9)
    req4 = Notification('my_notification', 1, 2, 3)
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
    assert isinstance(result[2], MethodExecutionError)
    assert result[2].code == -32602
    assert result[2].message == "Invalid params"
    assert result[3] is None


@pytest.mark.asyncio
async def test_magic_method(transport, transport_received, transport_to_send):
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
