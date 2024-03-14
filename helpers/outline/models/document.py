from datetime import datetime
from typing import Dict, List
from urllib.parse import urljoin
from uuid import UUID

from ..utils import dt_from_iso

from .user import User


__all__ = ["Document"]


class Document:
    """Class representing an Outline document

    Parameters
    ----------
    data : Dict
        The raw data to create the Document object from

    Attributes
    ----------
    data : Dict
        The raw data that was used to create the Document object
    id : str
        The unique id of the document
    share_id : UUID | None
        A share id for the document
    team_id : UUID
        Id of the team that the document is a part of
    collection_id : UUID
        Id of the collection that the document is a part of
    parent_document_id : UUID | None
        Id of the document's parent document, if any
    collaborator_ids : List[str]
        Ids of the collaborators of the document
    url : str
        Url to the document
    url_id : UUID
        Url Id of the document
    title : str
        Title of the document
    text : str
        Body text of the document
    revision : int
        How many times the document has been revised
    tasks : Dict
        The tasks of the document
    created_at : datetime
        When the document was created
    created_by : User
        The user who the document was created by
    updated_at : datetime
        The last time the document was updated
    updated_by : User
        The user who last updated the document
    last_viewed_at : datetime
        The last time the document was viewed
    published_at : datetime
        When the document was published
    archived_at : datetime | None
        When the document was archived, if any
    deleted_at : datetime | None
        When the document was deleted, if any
    template : bool
        If the document is a template
    template_id : UUID | None
        Id of the template, if any
    insights_enabled : bool
        If insights are enabled on this document
    full_width : bool
        If this document is full width
    """

    __slots__ = [
        "data",
        "id",
        "share_id",
        "team_id",
        "collection_id",
        "parent_document_id",
        "collaborator_ids",
        "url",
        "url_id",
        "title",
        "text",
        "revision",
        "tasks",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "last_viewed_at",
        "published_at",
        "archived_at",
        "deleted_at",
        "template",
        "template_id",
        "insights_enabled",
        "full_width",
    ]

    def __init__(self, data: Dict) -> None:
        self.data: Dict = data

        self.id: UUID = UUID(data["id"])
        self.share_id: UUID | None = UUID(data["shareId"]) if data.get("shareId") else None
        self.team_id: UUID = data["teamId"]
        self.collection_id: UUID = data["collectionId"]
        self.parent_document_id: UUID | None = UUID(data["parentDocumentId"]) if data.get("parentDocumentId") else None
        self.collaborator_ids: List[str] = [UUID(collaborator_id) for collaborator_id in data["collaboratorIds"]]

        self.url: str = data["url"]
        self.url_id: UUID = data["urlId"]

        self.title: str = data["title"]
        self.text: str = data["text"]
        self.revision: int = data["revision"]
        self.tasks: Dict = data["tasks"]

        self.created_at: datetime = dt_from_iso(data["createdAt"])
        self.created_by: User = User(data["createdBy"])

        self.updated_at: datetime = dt_from_iso(data["updatedAt"])
        self.updated_by: User = User(data["updatedBy"])

        self.last_viewed_at: datetime | None = dt_from_iso(data["lastViewedAt"]) if data.get("lastViewedAt") else None
        self.published_at: datetime = dt_from_iso(data["publishedAt"])
        self.archived_at: datetime | None = dt_from_iso(data["archivedAt"]) if data.get("archivedAt") else None
        self.deleted_at: datetime | None = dt_from_iso(data["deletedAt"]) if data.get("deletedAt") else None

        self.template: bool = data["template"]
        self.template_id: UUID | None = UUID(data["templateId"]) if data.get("templateId") else None
        self.insights_enabled: bool = data["insightsEnabled"]
        self.full_width: bool = data["fullWidth"]

    def full_url(self, base_url: str) -> str:
        return urljoin(base_url, self.url)
