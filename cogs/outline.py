from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple
import difflib
import math
import re
from textwrap import dedent, shorten

import discord
from discord.ext import commands
from discord import app_commands

from helpers import checks
from helpers.context import GuiduckContext
from helpers.pagination import Paginator
from helpers.utils import full_format_dt

import outline_api_wrapper as outline
from outline_api_wrapper.models.document import Document
from helpers.outline.checks import do_ephemeral, has_outline_access, has_document_access
from helpers.outline.constants import COLLECTION_NAMES, LINES_PER_PAGE, OPTIONS_LIMIT
from helpers.outline.converters import CollectionConverter, DocumentArgs, SearchDocumentArgs
from helpers.outline.decorators import remedy_args_bug, with_typing
from helpers.outline.exceptions import (
    CollectionNotFound,
    DocumentNotFound,
    MissingCommandPermission,
    OutlineException,
)


@dataclass
class SearchResult:
    context: str
    document: Document


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

    def document_to_embed(self, document: outline.Document) -> discord.Embed:
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
                Created At: {full_format_dt(document.created_at)}
                Updated At: {full_format_dt(document.updated_at)}
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

    def format_choice_label(self, document: outline.Document) -> str:
        collection = COLLECTION_NAMES.get(document.collection_id, "")
        return f"{collection.title()}　|　{document.title}"

    def search_collections(self, text: str, collections_list: List[str]) -> List[str]:
        substring_results = list(sorted([c for c in collections_list if text in c], key=lambda c: c.index(text)))
        close_results = difflib.get_close_matches(text, collections_list, n=OPTIONS_LIMIT)

        total_results = substring_results + close_results
        return total_results[:OPTIONS_LIMIT]

    def collections_sort_key(self, document: outline.Document) -> int:
        return list(COLLECTION_NAMES.keys()).index(document.collection_id)

    async def search_documents(
        self,
        query: str | None,
        collection_id: str | None,
        *,
        limit: Optional[int] = OPTIONS_LIMIT,
        context_limit: Optional[int] = 100,
        ranking_threshold: Optional[int] = 0.6,
    ) -> List[SearchResult]:
        """Search documents based on query"""

        query = query.strip().casefold()
        if not query:
            documents = await self.client.list_documents(collection_id=collection_id, limit=limit)
            documents.sort(key=self.collections_sort_key)
            results = [SearchResult(shorten(document.text, context_limit), document) for document in documents]
        else:
            results = await self.client.search_documents(query, collection_id=collection_id, limit=limit)
            results = [
                SearchResult(shorten(result.context, context_limit), result.document)
                for result in results
                if result.ranking >= ranking_threshold
            ]

        return results

    async def find_document(self, query: str | None, collection_id: str | None) -> Document:
        results = await self.search_documents(query, collection_id, limit=1)
        if not results:
            raise DocumentNotFound

        return results[0].document

    async def paginate_document(self, ctx: GuiduckContext, document: outline.Document):
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

        if not args.text:
            raise DocumentNotFound

        if outline.is_valid_uuid(args.text):
            # A specific document id was passed, either manually or via autocomplete
            try:
                doc = await self.client.fetch_document(args.text)
            except outline.NotFound:
                raise DocumentNotFound

            if await has_document_access(ctx, doc):
                return await self.paginate_document(ctx, doc)

        else:
            collection_id = args.collection
            doc = await self.find_document(args.text, collection_id)
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
        """Search or list documents from the Outline knowledge base.

        You must have the Trial Moderator role in order to use this.
        """

        if outline.is_valid_uuid(args.text):
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
            accessible_collections = await CollectionConverter.get_user_collections(ctx)
        except MissingCommandPermission as e:
            return [app_commands.Choice(name=str(e), value="")]

        if checks.passes_check(checks.is_admin, ctx):
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
            app_commands.Choice(name=self.format_choice_label(result.document), value=str(result.document.id))
            for result in search_results
        ]


async def setup(bot):
    await bot.add_cog(Outline(bot))
