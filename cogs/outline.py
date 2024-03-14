import contextlib
from datetime import datetime
import difflib
import importlib
import math
import os
import re
import sys
import textwrap
from typing import List, Optional
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
NO_PERMISSION_MESSAGE = "You do not have permission to use this command."
EPHEMERAL_REQUIRED_MESSAGE = "This command is restricted to staff categories only. However, you can use the slash command to view an ephemeral version anywhere."

COLLECTION_NAMES = {v: k for k, v in COLLECTION_IDS.items()}
ACCESSIBLE_COLLECTIONS = {
    checks.is_developer: ("development",),
    checks.is_trial_moderator: ("moderators", "moderator wiki"),
    checks.is_moderator: ("moderators", "moderator wiki"),
    checks.is_community_manager: ("management",),
}


def has_outline_access():
    async def predicate(ctx):
        possible_cols = await CollectionConverter.get_accessible_collections(ctx)
        if len(possible_cols) == 0:
            raise commands.CheckFailure(NO_PERMISSION_MESSAGE)
        return True

    return commands.check(predicate)


def format_dt(dt: datetime) -> str:
    return f"{discord.utils.format_dt(dt)} ({discord.utils.format_dt(dt, 'R')})"


class CollectionConverter(commands.Converter):
    @staticmethod
    async def get_accessible_collections(ctx: GuiduckContext) -> List[str]:
        """Get all collections accessible by user"""

        accessible_collections = []

        for check, collections in ACCESSIBLE_COLLECTIONS.items():
            with contextlib.suppress(commands.CheckAnyFailure):
                if await check().predicate(ctx):
                    accessible_collections.extend(collections)

        return list(dict.fromkeys(accessible_collections))

    @staticmethod
    async def get_default_collection(ctx: GuiduckContext) -> str:
        """Default collection to use for user when no collection is provided"""

        with contextlib.suppress(commands.CheckAnyFailure):
            if await checks.is_admin().predicate(ctx):
                return "all"

        accessible_collections = await CollectionConverter.get_accessible_collections(ctx)
        return COLLECTION_IDS[accessible_collections[0]]

    @staticmethod
    async def convert(ctx: GuiduckContext, argument: Optional[str] = None) -> str | None:
        """Convert collection name to string"""

        if not argument:
            return await CollectionConverter.get_default_collection(ctx)

        argument = argument.casefold()

        with contextlib.suppress(commands.CheckAnyFailure):
            if await checks.is_admin().predicate(ctx):
                if argument == "all":
                    return "all"

        accessible_collections = await CollectionConverter.get_accessible_collections(ctx)

        if argument not in accessible_collections:
            if argument in COLLECTION_IDS or argument == "all":
                raise commands.ArgumentParsingError("You do not have permission to view this collection.")
            raise commands.ArgumentParsingError("Collection not found.")

        return COLLECTION_IDS[argument]


class DocumentArgs(commands.FlagConverter, case_insensitive=True):
    collection: CollectionConverter = commands.flag(
        aliases=("col",),
        description="Search within collection",
        max_args=1,
        default=CollectionConverter.convert,
    )
    search: str = commands.flag(
        aliases=("s",),
        description="Search documents",
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
        embed = discord.Embed(
            title=document.title,
            color=discord.Color.blurple(),
            url=document.full_url(self.client.base_url),
        )
        embed.set_author(name=document.created_by.name, icon_url=document.created_by.avatar_url)
        embed.add_field(
            name="Information",
            value=textwrap.dedent(
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

        # This is temporary until this bug is fixed in discord.py (https://github.com/Rapptz/discord.py/issues/9641)
        for flag in args.get_flags().values():
            arg = getattr(args, flag.attribute)
            if callable(arg):
                setattr(args, flag.attribute, await discord.utils.maybe_coroutine(arg, ctx))

        ephemeral = await self.do_ephemeral(ctx)
        if ephemeral is None:
            return await ctx.reply(EPHEMERAL_REQUIRED_MESSAGE, mention_author=False)

        async with ctx.typing(ephemeral=ephemeral or args.ephemeral):
            collection_id = args.collection
            if not collection_id:
                return
            elif collection_id == "all":
                collection_id = None

            try:
                UUID(args.search)
            except ValueError:
                docs = await self.client.search_documents(args.search, collection_id, limit=1)
                if not docs:
                    return await ctx.send("Document not found.")
                doc = docs[0].document
            else:
                doc = await self.client.fetch_document(args.search)

            total_lines = len(doc.text.split("\n"))
            total_pages = math.ceil(total_lines / LINES_PER_PAGE)
            paginator = Paginator(self.document_to_embed(doc), total_pages, loop=False)
            await paginator.start(ctx)

    @document.autocomplete("collection")
    async def collection_autocomplete(self, interaction: discord.Interaction, current: str):
        ctx = await GuiduckContext.from_interaction(interaction)
        accessible_collections = await CollectionConverter.get_accessible_collections(ctx)

        with contextlib.suppress(commands.CheckAnyFailure):
            if await checks.is_admin().predicate(ctx):
                accessible_collections.append("all")

        current = current.strip().casefold()
        if not current:
            collections = accessible_collections
        else:
            collections = sorted(
                [c for c in accessible_collections if current in c], key=lambda c: c.index(current)
            ) or difflib.get_close_matches(current, accessible_collections, n=25)
            if not collections:
                return [app_commands.Choice(name=f"No collections found.", value="")]
        return [app_commands.Choice(name=collection.title(), value=collection) for collection in collections]

    @document.autocomplete("search")
    async def doc_search_autocomplete(self, interaction: discord.Interaction, current: str):
        ctx = await GuiduckContext.from_interaction(interaction)

        collection_id = await CollectionConverter.convert(ctx, interaction.namespace.collection)
        if not collection_id:
            return [app_commands.Choice(name=f"Invalid collection selected.", value="")]

        if collection_id == "all":
            collection_id = None

        current = current.strip().casefold()
        if not current:
            documents = await self.client.list_documents(collection_id=collection_id)
        else:
            results = await self.client.search_documents(current, collection_id=collection_id)
            if not results:
                return [app_commands.Choice(name=f"No documents found.", value="")]
            else:
                documents = [result.document for result in results if result.ranking > 0.8]

        return [app_commands.Choice(name=document.title, value=str(document.id)) for document in documents]


async def setup(bot):
    await bot.add_cog(Outline(bot))
