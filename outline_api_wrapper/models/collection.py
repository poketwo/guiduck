from datetime import datetime
from typing import Dict
from urllib.parse import urljoin
from uuid import UUID

from outline_api_wrapper.constants import Direction, Permission

from ..utils import dt_from_iso

__all__ = ["Collection"]


class Collection:
    """Class representing an Outline collection

    Parameters
    ----------
    data : Dict
        The raw data to create the Collection object from

    Attributes
    ----------
    data : Dict
        The raw data that was used to create the Collection object
    id : str
        The unique id of the document
    name : str
        The name of the collection
    description : str
        A description of the collection, may contain markdown formatting
    sort : dict
        The sort of documents in the collection
        keys:
        - field
        - direction
    index : int
        The position of the collection in the sidebar
    color : int
        A color representing the collection, this is used to help make collections more identifiable in the UI
    icon : str
        A string that represents an icon in the outline-icons package
    permission: Permission
        What permission the user has in the collection (read/read_write)
    created_at : datetime
        When the collection was created
    updated_at : datetime
        The last time the collection was updated
    deleted_at : datetime | None
        When the collection was deleted, if any
    """

    __slots__ = [
        "data",
        "id",
        "name",
        "description",
        "sort",
        "index",
        "color",
        "icon",
        "permission",
        "created_at",
        "updated_at",
        "deleted_at",
    ]

    def __init__(self, data: Dict) -> None:
        self.data: Dict = data

        self.id: UUID = UUID(data["id"])

        self.name: str = data["name"]
        self.description: str = data["description"]
        self.sort: dict = {"field": data["sort"]["field"], "direction": Direction(data["sort"]["direction"])}
        self.index: int = data["index"]
        self.color: int = int(data["color"][1:], 16)  # [1:] because it starts with #
        self.icon: str = data["icon"]
        self.permission: Permission | None = Permission(data["permission"]) if data["permission"] else None

        self.created_at: datetime = dt_from_iso(data["createdAt"])
        self.updated_at: datetime = dt_from_iso(data["updatedAt"])
        self.deleted_at: datetime | None = dt_from_iso(data["deletedAt"]) if data.get("deletedAt") else None

    def __str__(self) -> str:
        return f"<Collection name={self.name} id={self.id}>"

    def __repr__(self) -> str:
        return str(self)

    def full_url(self, base_url: str) -> str:
        return urljoin(base_url, self.url)
