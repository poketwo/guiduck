from typing import Optional

from discord.ext import commands


class OutlineException(commands.CheckFailure):
    message = "An Outline command exception occurred"

    def __init__(self, message: Optional[str] = None, *args):
        super().__init__(message or self.message, *args)


class MissingCommandPermission(OutlineException):
    message = "You do not have permission to use this command."


class MissingDocumentPermission(OutlineException):
    message = "You do not have permission to view this document."


class MissingCollectionPermission(OutlineException):
    message = "You do not have permission to view this collection."


class NoDocumentsFound(OutlineException):
    message = "No documents found."


class NoCollectionsFound(OutlineException):
    message = "No collections found."


class EphemeralRequired(OutlineException):
    message = (
        "This command is restricted to staff categories only. However, you can use the"
        " slash command to view an ephemeral version anywhere."
    )
