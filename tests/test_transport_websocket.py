"""
Tests for the websocket transport.
"""

import functools

import pytest
import mock

from jsonrpc.client.transport.websocket import Transport


def mock_websockets_connect(func):
    """
    Decorator that mocks websockets.connect to return a mock client.

    The mocked connect function and the mock client are injected into the wrapped function.
    """
    async def wrapper(*args, **kwargs):
        # Create a mock for the websocket client
        ws_client = mock.AsyncMock()
        # Patch websocket.connect to return the mock client
        with mock.patch(
            'websockets.connect',
            new_callable = mock.AsyncMock,
            return_value = ws_client
        ) as ws_connect:
            return await func(ws_connect, ws_client, *args, **kwargs)
    return wrapper


@pytest.mark.asyncio
@mock_websockets_connect
async def test_send(ws_connect, ws_client):
    """
    Tests that the transport sends websocket data successfully.
    """
    request_content = "[1,2,3]"
    async with Transport("ws://test.endpoint") as transport:
        # Send the request
        await transport.send(request_content, 'application/json')
    # Check that websockets.connect was called correctly
    ws_connect.assert_awaited_once_with('ws://test.endpoint')
    # Check that the request content was correct
    ws_client.send.assert_awaited_once_with(request_content)
    # Check that the client was closed
    ws_client.close.assert_awaited_once()


@pytest.mark.asyncio
@mock_websockets_connect
async def test_receive(ws_connect, ws_client):
    """
    Tests that the transport receives and forwards websocket data successfully.
    """
    recv_messages = ["[1,2,3]", "[4,5,6]", "[7,8,9]"]
    # Mock recv so that it returns each of the messages when awaited
    ws_client.recv.side_effect = recv_messages
    async with Transport("ws://test.endpoint") as transport:
        # Get three messages from the transport
        receive_iter = transport.receive()
        results = [
            await receive_iter.__anext__(),
            await receive_iter.__anext__(),
            await receive_iter.__anext__(),
        ]
    # Check that websockets.connect was called correctly
    ws_connect.assert_awaited_once_with('ws://test.endpoint')
    # Assert that recv was awaited 3 times
    ws_client.recv.assert_awaited()
    assert ws_client.recv.await_count == 3
    # Assert that the results look correct
    assert results == recv_messages
    # Check that the client was closed
    ws_client.close.assert_awaited_once()
