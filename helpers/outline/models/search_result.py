from typing import Dict

from .document import Document


__all__ = ["DocumentSearchResult"]


class DocumentSearchResult:
    """Class representing a document search result

    Parameters
    ----------
    data : Dict
        The raw data to create the DocumentSearchResult object from

    Attributes
    ----------
    data : Dict
        The raw data that was used to create the DocumentSearchResult object
    context : str
        The part of the Document's title/text that matched the search query
    ranking : float
        The ranking of how close the search result was to the query
    document : Document
        The actual document from the search result
    """

    __slots__ = [
        "data",
        "context",
        "ranking",
        "document",
    ]

    def __init__(self, data: Dict) -> None:
        self.data: Dict = data

        self.context = data["context"]
        self.ranking = data["ranking"]
        self.document = Document(data["document"])
