import contextlib
from datetime import datetime
import difflib
import math
import re
from textwrap import dedent, shorten
from typing import List, Optional, Tuple
from uuid import UUID
import discord
from discord.ext import commands
from discord import app_commands

from config import OUTLINE_COLLECTION_IDS as COLLECTION_IDS
from helpers.context import GuiduckContext
from helpers import checks
from helpers import outline
from helpers.outline.models.document import Document
from helpers.pagination import Paginator


LINES_PER_PAGE = 15


class ERROR_MESSAGES:
    MISSING_PERMISSION = "You do not have permission to use this command."
    MISSING_COLLECTION_PERMISSION = "You do not have permission to view this collection."
    MISSING_DOCUMENT_PERMISSION = "You do not have permission to view this document."

    NO_DOCUMENTS = "No documents found."
    NO_COLLECTIONS = "No collections found."

    EPHEMERAL_REQUIRED = "This command is restricted to staff categories only. However, you can use the slash command to view an ephemeral version anywhere."


COLLECTION_NAMES = {v: k for k, v in COLLECTION_IDS.items()}
ACCESSIBLE_COLLECTIONS = {
    checks.is_developer: ("development",),
    checks.is_trial_moderator: ("moderators", "moderator wiki"),
    checks.is_moderator: ("moderators", "moderator wiki"),
    checks.is_community_manager: ("management",),
}


def has_outline_access():
    """Check if user has perms to use Outline things"""

    async def predicate(ctx):
        accessible_collections = await CollectionConverter.get_accessible_collections(ctx)
        if len(accessible_collections) == 0:
            raise MissingPermission
        return True

    return commands.check(predicate)


def is_outline_admin():
    """Check if user should have admin access"""

    return checks.is_admin()


def format_dt(dt: datetime) -> str:
    return f"{discord.utils.format_dt(dt)} ({discord.utils.format_dt(dt, 'R')})"


class OutlineException(commands.CheckFailure):
    pass


class MissingPermission(OutlineException):
    def __init__(self, message: Optional[str] = ERROR_MESSAGES.MISSING_PERMISSION, *args):
        super().__init__(message)


class MissingCollectionPermission(OutlineException):
    def __init__(self, message: Optional[str] = ERROR_MESSAGES.MISSING_COLLECTION_PERMISSION, *args):
        super().__init__(message)


class CollectionNotFound(OutlineException):
    def __init__(self, message: Optional[str] = ERROR_MESSAGES.NO_COLLECTIONS, *args):
        super().__init__(message)


class CollectionConverter(commands.Converter):
    """Converter to convert collection name to id"""

    @staticmethod
    async def get_accessible_collections(ctx: GuiduckContext) -> List[str]:
        """Get all collections accessible by user"""

        accessible_collections = []

        for check, collections in ACCESSIBLE_COLLECTIONS.items():
            with contextlib.suppress(commands.CheckAnyFailure):
                if await check().predicate(ctx):
                    accessible_collections.extend(collections)

        if not accessible_collections:
            raise MissingPermission

        return list(dict.fromkeys(accessible_collections))

    @staticmethod
    async def is_admin(ctx: GuiduckContext) -> bool:
        try:
            await is_outline_admin().predicate(ctx)
        except commands.CheckFailure:
            return False
        else:
            return True

    @staticmethod
    async def get_default_collection(ctx: GuiduckContext) -> str | None:
        """Default collection to use for user when no collection is provided"""

        if await CollectionConverter.is_admin(ctx):
            return None

        accessible_collections = await CollectionConverter.get_accessible_collections(ctx)
        return COLLECTION_IDS[accessible_collections[0]]

    @staticmethod
    async def convert(ctx: GuiduckContext, argument: Optional[str] = None) -> str | None:
        """Convert collection name to string"""

        if not argument:
            return await CollectionConverter.get_default_collection(ctx)

        argument = argument.strip().casefold()
        if await CollectionConverter.is_admin(ctx):
            if argument == "all":
                return None

        accessible_collections = await CollectionConverter.get_accessible_collections(ctx)

        if argument not in accessible_collections:
            if argument in COLLECTION_IDS or argument == "all":
                raise MissingCollectionPermission
            raise CollectionNotFound

        return COLLECTION_IDS[argument]


class DocumentArgs(commands.FlagConverter, case_insensitive=True):
    """Flags for the document command"""

    collection: CollectionConverter = commands.flag(
        aliases=("col",),
        description="Search within collection",
        max_args=1,
        default=CollectionConverter.convert,
    )
    search: str = commands.flag(
        aliases=("s",),
        description="Search documents (type a space to refresh based on selected collection)",
        max_args=1,
    )
    ephemeral: Optional[bool] = commands.flag(
        description="Send as an ephemeral message that only you can see",
        max_args=1,
        default=False,
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

    async def do_ephemeral(self, ctx: GuiduckContext) -> bool | None:
        """Checks if the command is being ran in Staff categories and whether to
        do ephemeral or not. Returns True/False if outside/in staff categories, and
        None if command was a message command and hence ephemeral not possible."""

        try:
            if await checks.staff_categories_only().predicate(ctx):
                ephemeral = False
        except commands.CheckFailure:
            if ctx.interaction:
                ephemeral = True  # Force ephemeral incase it's outside staff categories if app command
            else:
                return None
        return ephemeral

    def sort_by_collection(self, document: Document) -> int:
        return list(COLLECTION_NAMES.keys()).index(document.collection_id)

    def search_collections(self, text: str, collections_list: List[str]):
        substring_search = sorted([c for c in collections_list if text in c], key=lambda c: c.index(text))
        return substring_search or difflib.get_close_matches(text, collections_list, n=25)

    def format_choice_label(self, document: Document) -> str:
        collection = COLLECTION_NAMES.get(document.collection_id, "")
        return f"{collection.title()}　|　{document.title}"

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

    @has_outline_access()
    @commands.hybrid_group(
        "document",
        fallback="view",
        aliases=("doc", "docs"),
        usage='search: <search> collection: [collection="Moderator"]',
    )
    async def document(self, ctx: GuiduckContext, *, args: DocumentArgs):
        """View documents from the Outline Knowledge Base.

        You must have the Trial Moderator role in order to use this.
        """

        # This is temporary to set the function attrs until this bug is fixed in discord.py (https://github.com/Rapptz/discord.py/issues/9641)
        for flag in args.get_flags().values():
            arg = getattr(args, flag.attribute)
            if callable(arg):
                setattr(args, flag.attribute, await discord.utils.maybe_coroutine(arg, ctx))

        # Restrict the command to staff categories only
        # Or force ephemeral if possible
        ephemeral = await self.do_ephemeral(ctx)
        if ephemeral is None:
            return await ctx.reply(ERROR_MESSAGES.EPHEMERAL_REQUIRED, mention_author=False)

        async with ctx.typing(ephemeral=ephemeral or args.ephemeral):
            collection_id = args.collection

            # TODO: Make this a converter so that it can be reused for search
            try:
                UUID(args.search)
            except ValueError:
                docs = await self.client.search_documents(args.search, collection_id, limit=1)
                if not docs:
                    return await ctx.send(ERROR_MESSAGES.NO_DOCUMENTS)
                doc = docs[0].document
            else:
                try:
                    doc = await self.client.fetch_document(args.search)
                except outline.NotFound:
                    return await ctx.send(ERROR_MESSAGES.NO_DOCUMENTS)
                else:
                    accessible_collections = await CollectionConverter.get_accessible_collections(ctx)
                    if doc.collection_id not in accessible_collections:
                        return await ctx.send(ERROR_MESSAGES.MISSING_DOCUMENT_PERMISSION)

            total_lines = len(doc.text.split("\n"))
            total_pages = math.ceil(total_lines / LINES_PER_PAGE)
            paginator = Paginator(self.document_to_embed(doc), total_pages, loop=False)
            await paginator.start(ctx)

    @document.autocomplete("collection")
    async def collection_autocomplete(self, interaction: discord.Interaction, current: str):
        ctx = await GuiduckContext.from_interaction(interaction)

        try:
            accessible_collections = await CollectionConverter.get_accessible_collections(ctx)
        except MissingPermission as e:
            return [app_commands.Choice(name=str(e), value="")]

        if await CollectionConverter.is_admin(ctx):
            accessible_collections.append("all")

        current = current.strip().casefold()
        if not current:
            collections = accessible_collections
        else:
            collections = self.search_collections(current, accessible_collections)
            if not collections:
                return [app_commands.Choice(name=ERROR_MESSAGES.NO_COLLECTIONS, value="")]
        return [app_commands.Choice(name=collection.title(), value=collection) for collection in collections]

    @document.autocomplete("search")
    async def doc_search_autocomplete(self, interaction: discord.Interaction, current: str):
        ctx = await GuiduckContext.from_interaction(interaction)

        try:
            collection_id = await CollectionConverter.convert(ctx, interaction.namespace.collection)
        except (MissingPermission, CollectionNotFound) as e:
            return [app_commands.Choice(name=str(e), value="")]

        search_results = await self.search_documents(current, collection_id)
        if not search_results:
            return [app_commands.Choice(name=ERROR_MESSAGES.NO_DOCUMENTS, value="")]

        return [
            app_commands.Choice(name=self.format_choice_label(document), value=str(document.id))
            for context, document in search_results
        ]


async def setup(bot):
    await bot.add_cog(Outline(bot))
