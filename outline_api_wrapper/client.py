from __future__ import annotations
from typing import List, Optional
from uuid import UUID

import aiohttp


from .constants import DateFilter, Endpoint, Direction
from .http import HTTPClient, RequestMethod
from .models.search_result import DocumentSearchResult
from .models.document import Document
from .errors import BadRequest, NotFound


__all__ = [
    "Client",
]


class Client:
    """Client to connect and interact with the Outline API

    Parameters
    ----------
    base_url : str
        The base URL of the Outline app whose API to use.
    session : Optional[aiohttp.ClientSession]
        The aiohttp session to use to make requests. Creates a new one if not provided.

    Attributes
    ----------
    base_url : str
        The base URL of the Outline app whose API is being used.
    session : Optional[aiohttp.ClientSession]
        The aiohttp session being used to make requests.
    http_client : Optional[HTTPClient]
        The http client to make requests to the API.
    """

    __slots__ = [
        "base_url",
        "session",
        "http_client",
    ]

    def __init__(
        self, base_url: str, api_token: Optional[str] = None, *, session: Optional[aiohttp.ClientSession] = None
    ):
        self.base_url: str = base_url
        self.session: aiohttp.ClientSession | None = session

        self.http_client = HTTPClient(base_url, api_token, session=session)

    async def fetch_document(self, document_id: Optional[str] = None, *, share_id: Optional[UUID] = None) -> Document:
        """Fetch a document using its ID or Share ID

        Parameters
        ----------
        document_id : str
            The id of the document to fetch.
        share_id : Optional[UUID]
            A share id of the document. Does not require authentication if.
        """

        if share_id:
            authentication = False
            data = {"shareId": share_id}
        else:
            authentication = True
            data = {"id": document_id}

        doc_data = await self.http_client.request(
            RequestMethod.POST, Endpoint.RETRIEVE_DOCUMENT, data=data, authentication=authentication
        )
        return Document(doc_data["data"])

    async def search_documents(
        self,
        query: str,
        collection_id: Optional[UUID] = None,
        *,
        user_id: Optional[UUID] = None,
        offset: Optional[int] = None,
        limit: Optional[int] = 25,
        include_archived: Optional[bool] = None,
        include_drafts: Optional[bool] = None,
        date_filter: Optional[DateFilter] = None,
    ) -> List[DocumentSearchResult]:
        """Search documents

        Parameters
        ----------
        query : str
            The search query.
        collection_id : Optional[UUID]
            The id of the collection whose documents to search within.
        user_id : Optional[UUID]
            Any documents that have not been edited by the user identifier will be filtered out.
        offset : Optional[int]
            How many results to skip when searching. Useful for pagination.
        limit : Optional[int] = 25
            How many results to retrieve. Useful for pagination.
        """

        data = {"query": query}

        if collection_id is not None:
            data["collectionId"] = collection_id
        if user_id is not None:
            data["userId"] = user_id
        if offset is not None:
            data["offset"] = offset
        if limit is not None:
            data["limit"] = limit
        if include_archived is not None:
            data["includeArchived"] = include_archived
        if include_drafts is not None:
            data["includeDrafts"] = include_drafts
        if date_filter is not None:
            data["dateFilter"] = date_filter

        try:
            search_data = await self.http_client.request(
                RequestMethod.POST, Endpoint.SEARCH_DOCUMENTS, data=data, authentication=True
            )
        except (NotFound, BadRequest):
            return []
        else:
            return [DocumentSearchResult(search_result) for search_result in search_data["data"]]

    async def list_documents(
        self,
        collection_id: Optional[UUID] = None,
        *,
        user_id: Optional[UUID] = None,
        backlink_document_id: Optional[UUID] = None,
        parent_document_id: Optional[UUID] = None,
        offset: Optional[int] = None,
        limit: Optional[int] = 25,
        sort: Optional[str] = None,
        direction: Optional[Direction] = None,
        template: Optional[bool] = None,
    ) -> List[DocumentSearchResult]:
        """Search documents

        Parameters
        ----------
        collection_id : Optional[UUID]
            The id of the collection whose documents to list.
        user_id : Optional[UUID]
            The id of the user whose documents to list.
        backlink_document_id : Optional[UUID]
            The id of the backlink document whose documents to list.
        parent_document_id : Optional[UUID]
            The id of the parent document whose documents to list.
        template : Optional[bool]
            If it should list only tempalates.
        offset : Optional[int]
            How many results to skip when searching. Useful for pagination.
        limit : Optional[int] = 25
            How many results to retrieve. Useful for pagination.
        sort : Optional[str]
            Which field/attribute of the documents to sort by.
        direction : Optional[Direction]
            Which direction to sort by.
        """

        data = {}

        if collection_id is not None:
            data["collectionId"] = collection_id
        if user_id is not None:
            data["userId"] = user_id
        if backlink_document_id is not None:
            data["backlinkDocumentId"] = backlink_document_id
        if parent_document_id is not None:
            data["parentDocumentId"] = parent_document_id
        if template is not None:
            data["template"] = template
        if offset is not None:
            data["offset"] = offset
        if limit is not None:
            data["limit"] = limit
        if sort is not None:
            data["sort"] = sort
        if direction is not None:
            data["direction"] = direction.value

        try:
            documents = await self.http_client.request(
                RequestMethod.POST, Endpoint.LIST_DOCUMENTS, data=data, authentication=True
            )
        except (NotFound, BadRequest):
            return []
        else:
            return [Document(doc_data) for doc_data in documents["data"]]
