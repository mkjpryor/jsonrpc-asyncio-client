"""
Module containing exceptions raised by the JSON-RPC client.
"""


class TransportExhausted(RuntimeError):
    """
    Raised when the transport is exhausted, i.e. the receive async iterator
    stops, before a request is resolved with a corresponding response.
    """
