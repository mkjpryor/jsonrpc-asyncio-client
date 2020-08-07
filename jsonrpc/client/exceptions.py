"""
Module containing exceptions raised by the JSON-RPC client.
"""


class JsonRpcClientException(Exception):
    """
    Base class for all JSON-RPC client exceptions.
    """


class InvalidRequest(JsonRpcClientException):
    """
    Raised when there is an error with the way a request is constructed.
    """


class MethodExecutionError(JsonRpcClientException):
    """
    Raised when there is an error executing a JSON-RPC method on the server.
    """
    def __init__(self, code, message, data):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(self.message)
