"""
Tests for the HTTP transport.
"""

import base64
import json

import httpx
import pytest

from jsonrpc.client.transport.http import Transport


@pytest.mark.asyncio
async def test_success_response(httpx_mock):
    """
    Tests that the correct requests are made and content returned
    for a successful response.
    """
    request_content = json.dumps([1, 2, 3])
    response_content = json.dumps([4, 5, 6])
    # We expect a POST request, and return a success response
    httpx_mock.add_response(
        url = "http://test.endpoint",
        method = "POST",
        match_headers = {'Content-Type': 'application/json'},
        data = response_content
    )
    async with Transport("http://test.endpoint") as transport:
        response_data = await transport.request(request_content, 'application/json')
    # Check that the request content was correct
    request = httpx_mock.get_request()
    assert request.read().decode() == request_content
    # Check that the response content is correct
    assert response_data == response_content


@pytest.mark.asyncio
async def test_failed_response(httpx_mock):
    """
    Tests that a non-200 response raises an error.
    """
    request_content = json.dumps([1, 2, 3])
    # Pretend that the JSON-RPC endpoint doesn't exist
    httpx_mock.add_response(status_code = 404)
    with pytest.raises(httpx.HTTPError) as excinfo:
        async with Transport("http://test.endpoint") as transport:
            response_data = await transport.request(request_content, 'application/json')
    # Check that the expected exception was raised
    assert excinfo.value.response.status_code == 404
    # Check that the request content was correct
    request = httpx_mock.get_request()
    assert request.read().decode() == request_content


@pytest.mark.asyncio
async def test_custom_client_args(httpx_mock):
    """
    Tests that the correct request is made when custom args are specified.
    """
    username = 'user'
    password = 'password123'
    auth_bytes = f'{username}:{password}'.encode('utf-8')
    auth_b64 = base64.b64encode(auth_bytes).decode('utf-8')
    request_content = json.dumps([1, 2, 3])
    response_content = json.dumps([4, 5, 6])
    # We expect a POST request, and return a success response
    httpx_mock.add_response(
        url = "http://test.endpoint",
        method = "POST",
        match_headers = {
            'Content-Type': 'application/json',
            'X-Custom-Header': 'some-value',
            'Authorization': f'Basic {auth_b64}'
        },
        data = response_content
    )
    async with Transport(
        "http://test.endpoint",
        headers = {'X-Custom-Header': 'some-value'},
        auth = (username, password)
    ) as transport:
        response_data = await transport.request(request_content, 'application/json')
    # Check that the request content was correct
    request = httpx_mock.get_request()
    assert request.read().decode() == request_content
    # Check that the response content is correct
    assert response_data == response_content
