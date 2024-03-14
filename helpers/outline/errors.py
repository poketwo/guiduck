from typing import Dict, Optional, Union
import aiohttp


__all__ = ["HTTPException", "AuthenticationError", "NotFound"]


class HTTPException(Exception):
    """Exception that is raised for HTTP request related failures

    Parameters
    ----------
    response : aiohttp.ClientResponse
        The request response
    data : Optional[Union[Dict, str]]
        The data received from the request
    """

    def __init__(
        self,
        response: aiohttp.ClientResponse,
        data: Optional[Union[Dict, str]],
    ):
        self.response: aiohttp.ClientResponse = response
        self.status: int = response.status
        self.code: int
        self.text: str

        if isinstance(data, dict):
            self.code = data.get("status", 0)
            self.text = data.get("message", "")
        else:
            self.text = data or ""
            self.code = 0

        message = f"{self.response.status} {self.response.reason} ({self.code})"
        if len(self.text):
            message += f": {self.text}"

        super().__init__(message)


class AuthenticationError(HTTPException):
    pass


class NotFound(HTTPException):
    pass


class BadRequest(HTTPException):
    pass
