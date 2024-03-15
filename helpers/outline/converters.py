from typing import Dict, Optional
from discord.ext import commands

from helpers import checks
from helpers.context import GuiduckContext
from .constants import ACCESSIBLE_COLLECTIONS, COLLECTION_IDS, DEFAULT_COLLECTION
from .exceptions import MissingCollectionPermission, NoCollectionsFound, MissingCommandPermission


class CollectionConverter(commands.Converter):
    """Converter to convert Outline collection name to id"""

    @staticmethod
    async def get_user_collections(ctx: GuiduckContext) -> Dict[str, str]:
        """Get all collections accessible by user"""

        accessible_collections = {}

        for check, collections in ACCESSIBLE_COLLECTIONS.items():
            if await checks.passes_check(check, ctx):
                for collection in collections:
                    accessible_collections[collection] = COLLECTION_IDS[collection]

        if not accessible_collections:
            raise MissingCommandPermission

        return accessible_collections

    @staticmethod
    async def get_default_collection(ctx: GuiduckContext) -> str | None:
        """Default collection to use for user when no collection is provided"""

        if await checks.passes_check(checks.is_admin, ctx):
            return None

        accessible_collections = await CollectionConverter.get_user_collections(ctx)
        return list(accessible_collections.values())[0]

    @staticmethod
    async def convert(ctx: GuiduckContext, argument: Optional[str] = None) -> str | None:
        """Convert collection name to string"""

        if not argument:
            return await CollectionConverter.get_default_collection(ctx)

        argument = argument.strip().casefold()
        if await checks.passes_check(checks.is_admin, ctx):
            if argument == "all":
                return None

        accessible_collections = await CollectionConverter.get_user_collections(ctx)

        if argument not in accessible_collections:
            if argument in COLLECTION_IDS or argument == "all":
                raise MissingCollectionPermission
            raise NoCollectionsFound
        else:
            return accessible_collections[argument]


class DocumentArgs(commands.FlagConverter):
    """Base flags for the Outline document commands"""

    MSG_CMD_USAGE = f'text: <text> collection: [collection="{DEFAULT_COLLECTION.title()}"]'
    text: str = commands.flag(
        aliases=("t", "txt"),
        description="Search for text in documents (type a space to refresh)",
        max_args=1,
    )
    collection: CollectionConverter = commands.flag(
        aliases=("col",),
        description="Search within collection",
        max_args=1,
        default=CollectionConverter.convert,
    )
    ephemeral: Optional[bool] = commands.flag(
        description="Send as an ephemeral message that only you can see. This is forced True if outside Staff Categories.",
        max_args=1,
        default=False,
    )


class SearchDocumentArgs(DocumentArgs):
    """Flags for the Outline document search command"""

    MSG_CMD_USAGE = f'text: [text] collection: [collection="{DEFAULT_COLLECTION.title()}"]'
    text: str = commands.flag(
        aliases=("t", "txt"),
        description="Search for text in documents (type a space to refresh)",
        max_args=1,
        default="",
    )
