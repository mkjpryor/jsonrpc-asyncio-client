"""
Tests for the request-response transport base class.
"""

import asyncio

import pytest
import mock

from jsonrpc.client.transport.base import RequestResponseTransport


@pytest.mark.asyncio
async def test_send_receive():
    """
    Tests that send and receive work correctly for a request-response transport
    as long as request behaves correctly.
    """
    Transport = type(
        'Transport',
        (RequestResponseTransport, ),
        dict(request = mock.AsyncMock())
    )
    # Make a mock from the request/response class
    transport = Transport()
    # Mock request to return the responses each time it is called
    transport.request.side_effect = ("[4,5,6]", "[10,11,12]", "")
    # Send all the requests
    await transport.send("[1,2,3]", 'application/json')
    await transport.send("[7,8,9]", 'application/json')
    await transport.send("[13,14,15]", 'application/json')
    # Assert that request was called correctly
    transport.request.assert_has_calls([
        mock.call("[1,2,3]", "application/json"),
        mock.call("[7,8,9]", "application/json"),
        mock.call("[13,14,15]", "application/json")
    ])
    # Check that only the non-empty responses come out
    receive_iter = transport.receive()
    assert await receive_iter.__anext__() == "[4,5,6]"
    assert await receive_iter.__anext__() == "[10,11,12]"
    # Fetching a third item should timeout even though we sent three requests
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(receive_iter.__anext__(), timeout = 1.0)
