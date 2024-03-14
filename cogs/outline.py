from __future__ import annotations

from datetime import datetime
import difflib
import functools
import math
import re
from textwrap import dedent, shorten
from typing import Any, Callable, Dict, List, Optional, Tuple
import discord
from discord.ext import commands
from discord import app_commands

from config import OUTLINE_COLLECTION_IDS as COLLECTION_IDS
from helpers.context import GuiduckContext
from helpers import checks
from helpers import outline
from helpers.outline.models.document import Document
from helpers.outline.utils import is_valid_uuid
from helpers.pagination import Paginator


LINES_PER_PAGE = 15
DOCS_PER_PAGE = 5


COLLECTION_NAMES = {v: k for k, v in COLLECTION_IDS.items()}
ACCESSIBLE_COLLECTIONS = {
    checks.is_developer: ("development",),
    checks.is_trial_moderator: ("moderators", "moderator wiki"),
    checks.is_moderator: ("moderators", "moderator wiki"),
    checks.is_community_manager: ("management",),
}
DEFAULT_COLLECTION = ACCESSIBLE_COLLECTIONS[checks.is_trial_moderator][0]


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


class DocumentNotFound(OutlineException):
    message = "No documents found."


class CollectionNotFound(OutlineException):
    message = "No collections found."


class EphemeralRequired(OutlineException):
    message = (
        "This command is restricted to staff categories only. However, you can use the"
        " slash command to view an ephemeral version anywhere."
    )


def format_dt(dt: datetime) -> str:
    return f"{discord.utils.format_dt(dt)} ({discord.utils.format_dt(dt, 'R')})"


def has_outline_access():
    """Check if user has perms to use Outline things"""

    async def predicate(ctx):
        accessible_collections = await CollectionConverter.get_accessible_collections(ctx)
        if len(accessible_collections) == 0:
            raise MissingCommandPermission
        return True

    return commands.check(predicate)


def is_outline_admin():
    """Check if user should have admin access"""

    return checks.is_admin()


async def passes_check(check: Callable[[GuiduckContext], Any], ctx: GuiduckContext) -> bool:
    try:
        await check().predicate(ctx)
    except commands.CheckFailure:
        return False
    else:
        return True


async def do_ephemeral(ctx: GuiduckContext):
    ephemeral_arg = (ctx.interaction.namespace if ctx.interaction else list(ctx.kwargs.values())[0]).ephemeral

    if await passes_check(checks.staff_categories_only, ctx):
        ephemeral = False
    else:
        if ctx.interaction:
            ephemeral = True  # Force ephemeral incase it's outside staff categories if app command
        else:
            raise EphemeralRequired

    return ephemeral or ephemeral_arg


def with_typing(do_ephemeral: Callable[[GuiduckContext], bool]):
    """Run command with calling ctx.typing, and make it ephemeral depending on check"""

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            ctx = discord.utils.find(lambda a: isinstance(a, commands.Context), args)
            ephemeral = await do_ephemeral(ctx)

            try:
                async with ctx.typing(ephemeral=ephemeral):
                    return await func(*args, **kwargs)
            except discord.InteractionResponded:
                return await func(*args, **kwargs)

        return wrapper

    return decorator


def remedy_args_bug(func):
    """This decorators is to remedy a bug in discord.py (https://github.com/Rapptz/discord.py/issues/9641)
    that makes it so that callable default values of flags aren't called in case of slash commands. So this decorator
    sets these for the FlagConverter object before the command's callback is ran."""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        ctx = discord.utils.find(lambda a: isinstance(a, GuiduckContext), args)
        flags = discord.utils.find(lambda k: isinstance(k, commands.FlagConverter), kwargs.values())

        if flags is not None:
            for flag in flags.get_flags().values():
                arg = getattr(flags, flag.attribute)
                if callable(arg):
                    setattr(flags, flag.attribute, await discord.utils.maybe_coroutine(arg, ctx))

        return await func(*args, **kwargs)

    return wrapper


class CollectionConverter(commands.Converter):
    """Converter to convert collection name to id"""

    @staticmethod
    async def get_accessible_collections(ctx: GuiduckContext) -> Dict[str, str]:
        """Get all collections accessible by user"""

        accessible_collections = {}

        for check, collections in ACCESSIBLE_COLLECTIONS.items():
            if await passes_check(check, ctx):
                for collection in collections:
                    accessible_collections[collection] = COLLECTION_IDS[collection]

        if not accessible_collections:
            raise MissingCommandPermission

        return accessible_collections

    @staticmethod
    async def get_default_collection(ctx: GuiduckContext) -> str | None:
        """Default collection to use for user when no collection is provided"""

        if await passes_check(is_outline_admin, ctx):
            return None

        accessible_collections = await CollectionConverter.get_accessible_collections(ctx)
        return list(accessible_collections.values())[0]

    @staticmethod
    async def convert(ctx: GuiduckContext, argument: Optional[str] = None) -> str | None:
        """Convert collection name to string"""

        if not argument:
            return await CollectionConverter.get_default_collection(ctx)

        argument = argument.strip().casefold()
        if await passes_check(is_outline_admin, ctx):
            if argument == "all":
                return None

        accessible_collections = await CollectionConverter.get_accessible_collections(ctx)

        if argument not in accessible_collections:
            if argument in COLLECTION_IDS or argument == "all":
                raise MissingCollectionPermission
            raise CollectionNotFound
        else:
            return accessible_collections[argument]


class DocumentArgs(commands.FlagConverter):
    """Base flags for the document commands"""

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
    """Flags for the document search command"""

    MSG_CMD_USAGE = f'text: [text] collection: [collection="{DEFAULT_COLLECTION.title()}"]'
    text: str = commands.flag(
        aliases=("t", "txt"),
        description="Search for text in documents (type a space to refresh)",
        max_args=1,
        default="",
    )


class Outline(commands.Cog):
    """For interfacing with Outline."""

    def __init__(self, bot):
        self.bot = bot
        self.client = outline.Client(
            self.bot.config.OUTLINE_BASE_URL,
            self.bot.config.OUTLINE_API_TOKEN,
            session=self.bot.http_session,
        )

    def translate_markdown(self, text: str) -> str:
        """Method to translate Outline markdown syntax to Discord markdown syntax"""

        # Replace double line breaks in front of headers with single
        text = re.sub(r"\n\n(#+ )", r"\n\1", text)

        # Replace highlights with italics
        text = re.sub(r"==(.+?)==", r"*\1*", text)

        # Replace new command lines
        while re.search(command_symbol := "\n\\\+\n", text) is not None:
            text = re.sub(command_symbol, "\n", text)

        return text

    def document_to_embed(self, document: Document) -> discord.Embed:
        """Create an embed from an Outline Document object. Returns function required to paginate."""

        embed = discord.Embed(
            title=document.title,
            color=discord.Color.blurple(),
            url=document.full_url(self.client.base_url),
        )
        embed.set_author(name=document.created_by.name, icon_url=document.created_by.avatar_url)
        embed.add_field(
            name="Information",
            value=dedent(
                f"""
                Created At: {format_dt(document.created_at)}
                Updated At: {format_dt(document.updated_at)}
                """
            ),
            inline=False,
        )

        def get_page(pidx: Optional[int] = 0) -> discord.Embed:
            total_lines = len(document.text.split("\n"))
            total_pages = math.ceil(total_lines / LINES_PER_PAGE)
            offset = pidx * LINES_PER_PAGE
            limit = offset + LINES_PER_PAGE

            text = self.translate_markdown(document.text)
            lines = text.split("\n")[offset:limit]
            embed.description = "\n".join(lines)
            embed.set_footer(text=f"Page {pidx + 1}/{total_pages}")

            return embed

        return get_page

    def format_choice_label(self, document: Document) -> str:
        collection = COLLECTION_NAMES.get(document.collection_id, "")
        return f"{collection.title()}　|　{document.title}"

    def sort_by_collection(self, document: Document) -> int:
        return list(COLLECTION_NAMES.keys()).index(document.collection_id)

    def search_collections(self, text: str, collections_list: List[str]):
        substring_search = sorted([c for c in collections_list if text in c], key=lambda c: c.index(text))
        return substring_search or difflib.get_close_matches(text, collections_list, n=25)

    async def search_documents(
        self,
        query: str | None,
        collection_id: str | None,
        *,
        context_limit: Optional[int] = 100,
        ranking_threshold: Optional[int] = 0.6,
    ) -> List[Tuple[str, Document]]:
        """Search documents based on query"""

        query = query.strip().casefold()
        if not query:
            documents = await self.client.list_documents(collection_id=collection_id)
            documents.sort(key=self.sort_by_collection)
            results = [(shorten(document.text, context_limit), document) for document in documents]
        else:
            results = await self.client.search_documents(query, collection_id=collection_id)
            results = [
                (shorten(result.context, context_limit), result.document)
                for result in results
                if result.ranking >= ranking_threshold
            ]

        return results

    async def has_document_access(self, ctx: GuiduckContext, document: Document) -> bool:
        accessible_collections = await CollectionConverter.get_accessible_collections(ctx)
        if document.collection_id not in accessible_collections.values():
            raise MissingCollectionPermission
        return True

    async def paginate_document(self, ctx: GuiduckContext, document: Document):
        total_lines = len(document.text.split("\n"))
        total_pages = math.ceil(total_lines / LINES_PER_PAGE)
        paginator = Paginator(self.document_to_embed(document), total_pages, loop=False)
        await paginator.start(ctx)

    @has_outline_access()
    @commands.hybrid_group(
        "document",
        fallback="view",
        aliases=("doc", "docs"),
        usage=DocumentArgs.MSG_CMD_USAGE,
    )
    @with_typing(do_ephemeral=do_ephemeral)
    @remedy_args_bug
    async def document(self, ctx: GuiduckContext, *, args: DocumentArgs):
        """View a document from the Outline staff knowledge base.

        You must have the Trial Moderator role in order to use this.
        """

        if is_valid_uuid(args.text):
            # A specific document id was passed, either manually or via autocomplete
            try:
                doc = await self.client.fetch_document(args.text)
            except outline.NotFound:
                raise DocumentNotFound

            if await self.has_document_access(ctx, doc):
                return await self.paginate_document(ctx, doc)

        else:
            collection_id = args.collection
            docs = await self.client.search_documents(args.text, collection_id, limit=1)
            if not docs:
                raise DocumentNotFound

            doc = docs[0].document
            await self.paginate_document(ctx, doc)

    @has_outline_access()
    @document.command(
        "search",
        aliases=("list",),
        usage=SearchDocumentArgs.MSG_CMD_USAGE,
    )
    @with_typing(do_ephemeral=do_ephemeral)
    @remedy_args_bug
    async def document_search(self, ctx: GuiduckContext, *, args: SearchDocumentArgs):
        """Search or list documents from the Outline Knowledge Base.

        You must have the Trial Moderator role in order to use this.
        """

        if is_valid_uuid(args.text):
            return await ctx.invoke(self.document, args=args)

        search_results = await self.search_documents(args.text, args.collection)
        if not search_results:
            raise DocumentNotFound

        raise OutlineException("Work in progress!")  # TODO: Complete & Test

    @document.autocomplete("collection")
    @document_search.autocomplete("collection")
    async def collection_autocomplete(self, interaction: discord.Interaction, current: str):
        ctx = await GuiduckContext.from_interaction(interaction)

        try:
            accessible_collections = await CollectionConverter.get_accessible_collections(ctx)
        except MissingCommandPermission as e:
            return [app_commands.Choice(name=str(e), value="")]

        if passes_check(is_outline_admin, ctx):
            accessible_collections["all"] = None

        current = current.strip().casefold()
        if not current:
            collections = accessible_collections
        else:
            collections = self.search_collections(current, list(accessible_collections.keys()))
            if not collections:
                return [app_commands.Choice(name=CollectionNotFound.message, value="")]

        return [app_commands.Choice(name=collection.title(), value=collection) for collection in collections]

    @document.autocomplete("text")
    @document_search.autocomplete("text")
    async def doc_search_autocomplete(self, interaction: discord.Interaction, current: str):
        ctx = await GuiduckContext.from_interaction(interaction)

        try:
            collection_id = await CollectionConverter.convert(ctx, interaction.namespace.collection)
        except (MissingCommandPermission, CollectionNotFound) as e:
            return [app_commands.Choice(name=str(e), value="")]

        search_results = await self.search_documents(current, collection_id)
        if not search_results:
            return [app_commands.Choice(name=DocumentNotFound.message, value="")]

        return [
            app_commands.Choice(name=self.format_choice_label(document), value=str(document.id))
            for context, document in search_results
        ]


async def setup(bot):
    await bot.add_cog(Outline(bot))
