from datetime import datetime
from typing import Dict
from uuid import UUID

from ..utils import dt_from_iso


__all__ = ["User"]


class User:
    """Class representing an Outline user

    Parameters
    ----------
    data : Dict
        The raw data to create the User object from

    Attributes
    ----------
    data : Dict
        The raw data that was used to create the User object
    id : UUID
        The unique uuid of the user
    name : str
        The name of the user
    avatar_url : str | None
        The url of the avatar of the user, if any
    color : int | None
        The integer representation of the hex code of the color of the user, if any
    is_admin : bool
        If the user is an admin
    is_suspended : bool
        If the user is suspended
    is_viewer : bool
        If the user is a viewer
    created_at : datetime
        The datetime object of when the user account was created
    last_active_at : datetime
        The datetime object of when the user was last active
    updated_at : datetime | None
        The datetime object of when the user account was last updated, if any
    """

    __slots__ = [
        "data",
        "id",
        "name",
        "avatar_url",
        "color",
        "is_admin",
        "is_suspended",
        "is_viewer",
        "created_at",
        "last_active_at",
        "updated_at",
    ]

    def __init__(self, data: Dict) -> None:
        self.data: Dict = data

        self.id: str = UUID(data["id"])

        self.name: str = data["name"]
        self.avatar_url: str | None = data.get("avatarUrl")
        self.color: int | None = int(data["color"].lstrip("#"), 16) if data.get("color") else None

        self.is_admin: bool = data["isAdmin"]
        self.is_suspended: bool = data["isSuspended"]
        self.is_viewer: bool = data["isViewer"]

        self.created_at: datetime = dt_from_iso(data["createdAt"])
        self.last_active_at: datetime = dt_from_iso(data["lastActiveAt"])

        self.updated_at: datetime | None = dt_from_iso(data["updatedAt"]) if data.get("updatedAt") else None

    def __str__(self) -> str:
        return f"<User id={self.id} name={self.name}>"

    def __repr__(self) -> str:
        return str(self)
