from enum import Enum


__all__ = [
    "Endpoint",
]


class Endpoint(Enum):
    LIST_COLLECTIONS = "collections.list"
    RETRIEVE_COLLECTION = "collections.info"

    LIST_DOCUMENTS = "documents.list"
    SEARCH_DOCUMENTS = "documents.search"
    RETRIEVE_DOCUMENT = "documents.info"


class DateFilter(Enum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"


class Direction(Enum):
    ASCENDING = "ASC"
    DESCENDING = "DESC"
