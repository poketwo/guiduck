from __future__ import annotations

from enum import Enum
import sys
from typing import Dict, Optional
from urllib.parse import urljoin

import aiohttp

from .constants import Endpoint
from .errors import AuthenticationError, BadRequest, HTTPException, NotFound


__all__ = [
    "RequestMethod",
    "StatusCode",
    "HTTPClient",
]


class RequestMethod(Enum):
    GET = "get"
    POST = "post"


class StatusCode(Enum):
    OK = 200

    MULTIPLE_CHOICES = 300

    BAD_REQUEST = 400
    UNAUTHENTICATED = 401
    NOT_FOUND = 404


class HTTPClient:
    """HTTP Client to make requests to the Outline API

    Parameters
    ----------
    base_url : str
        The base URL of the Outline app whose API to use.
    base_api_url : str
        The base API URL of base_url.
    api_token : Optional[str]
        The API Token to use when making requests. Optional, but necessary for requests that need authentication.
    session : Optional[aiohttp.ClientSession]
        The aiohttp session to use to make requests. Creates a new one if not provided.
    """

    __slots__ = [
        "base_url",
        "base_api_url",
        "api_token",
        "session",
        "user_agent",
    ]

    def __init__(
        self, base_url: str, api_token: Optional[str] = None, session: Optional[aiohttp.ClientSession] = None
    ) -> None:
        self.base_url: str = base_url
        self.base_api_url: str = urljoin(base_url, "api/")
        self.api_token: str = api_token
        self.session: aiohttp.ClientSession = session or aiohttp.ClientSession()

        self.user_agent = (
            f"Guiduck (https://github.com/poketwo/guiduck) "
            f"Python/{sys.version_info[0]}.{sys.version_info[1]} aiohttp/{aiohttp.__version__}"
        )

    async def _parse_response(self, response: aiohttp.ClientResponse) -> Dict | str:
        """Parses response to return data or raise error accordingly"""

        try:
            data = await response.json()
        except aiohttp.client_exceptions.ContentTypeError:
            data = await response.read()

        if StatusCode.MULTIPLE_CHOICES.value > response.status >= StatusCode.OK.value:
            return data
        elif response.status == StatusCode.BAD_REQUEST.value:
            raise BadRequest(response, data)
        elif response.status == StatusCode.UNAUTHENTICATED.value:
            raise AuthenticationError(response, data)
        elif response.status == StatusCode.NOT_FOUND.value:
            raise NotFound(response, data)
        else:
            raise HTTPException(response, data)

    async def request(
        self,
        method: RequestMethod,
        endpoint: Endpoint,
        *,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None,
        headers: Optional[Dict] = None,
        authentication: Optional[bool] = True,
    ) -> Dict | str:
        """The method to make asynchronous requests to the Outline API

        Parameters
        ----------
        method : RequestMethod
            The request method. Accepts RequestMethod enums only.
        endpoint : OutlineAPIEndpoint
            The Outline api endpoint to make the request to. Accepts OutlineAPIEndpoint enums only.
        data : Optional[Dict]
            The data to pass to the request.
        params : Optional[Dict]
            The params to pass to the request.
        headers : Optional[Dict]
            The headers to pass to the request.
        authentication : Optional[bool] = True
            Whether or not this request should require authentication.

        Returns
        -------
        Dict | str
            Returns the json data or the content returned by the response.

        Errors
        ------
        AuthenticationFailure
            Raised if client was unable to authorize the request (status code 401).
            E.g. invalid token
        NotFound
            Raised if the requested resource was not found (status code 404).
        HTTPException
            Raised for any other status code.
        """

        headers_final = {"Accept": "application/json", "User-Agent": self.user_agent}
        if authentication:
            if not self.api_token:
                raise AuthenticationError("To use functions that require authentication, please provide an API token")
            headers_final["Authorization"] = f"Bearer {self.api_token}"

        request_url = urljoin(self.base_api_url, endpoint.value)

        if headers is not None and isinstance(headers, dict):
            headers_final.update(headers)

        response = await self.session.request(
            method.value, request_url, params=params, json=data, headers=headers_final
        )

        return await self._parse_response(response)
