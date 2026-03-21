from __future__ import annotations

from typing import Dict, List, Optional
import difflib
import math
import re
from textwrap import dedent, shorten

import discord
from discord.ext import commands
from discord import app_commands

from helpers import checks
from helpers.context import GuiduckContext
from helpers.outline.pagination import SearchPaginator, SearchResult, format_document_label
from helpers.pagination import Paginator
from helpers.utils import full_format_dt, get_substring_matches, shorten_around

import outline_api_wrapper as outline
from helpers.outline.checks import do_ephemeral, has_outline_access, has_document_access
from helpers.outline.constants import (
    COLLECTION_NAMES,
    CONTEXT_HIGHLIGHT_PATTERN,
    CONTEXT_LIMIT,
    LINES_PER_PAGE,
    OPTIONS_LIMIT,
    RANKING_THRESHOLD,
)
from helpers.outline.converters import CollectionConverter, DocumentArgs, SearchDocumentArgs
from helpers.outline.decorators import remedy_args_bug, with_typing
from helpers.outline.exceptions import (
    MissingDocumentPermission,
    NoCollectionsFound,
    NoDocumentsFound,
    MissingCommandPermission,
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

        # Replace double line breaks in front of and after headers with single
        text = re.sub(r"\n\n?(#+ .+?)\n\n?", r"\n\1\n", text)

        # Replace h2 headers with h3 headers to make them less obtrusive
        text = re.sub(r"(^|\n)## ", r"\1### ", text)

        # Replace h1 headers with h2 headers to make them less obtrusive
        text = re.sub(r"(^|\n)# ", r"\1## ", text)

        # Replace highlights with italics
        text = re.sub(r"==(.+?)==", r"*\1*", text)

        # Replace new command lines
        while re.search(command_symbol := "\n\\\+\n", text) is not None:
            text = re.sub(command_symbol, "\n", text)

        # Trim leading/trailing whitespace characters
        text = re.sub(r"^\s+|\s+$", r"", text)

        return text

    def document_to_embed(self, document: outline.Document) -> discord.Embed:
        """Create an embed from an Outline Document object. Returns function required to paginate."""

        embed = discord.Embed(
            title=document.title,
            color=discord.Color.blurple(),
            url=document.full_url(self.client.base_url),
        )
        embed.set_author(name=document.created_by.name, icon_url=document.created_by.avatar_url)

        footer = [
            f"Updated at: {full_format_dt(document.updated_at, plain_text=True)}",
            f"Created at: {full_format_dt(document.created_at, plain_text=True)}",
        ]

        document_text = self.translate_markdown(document.text)
        def get_page(pidx: Optional[int] = 0) -> discord.Embed:
            total_lines = len(document_text.split("\n"))
            total_pages = math.ceil(total_lines / LINES_PER_PAGE)
            offset = pidx * LINES_PER_PAGE
            limit = offset + LINES_PER_PAGE

            lines = document_text.split("\n")[offset:limit]
            embed.description = "\n".join(lines)
            embed.set_footer(text="\n".join([*footer, f"Page {pidx + 1}/{total_pages}"]))

            return embed

        return get_page

    def search_collections(self, text: str, collections: Dict[str, str]) -> Dict[str, str]:
        text = text.strip().casefold()

        substring_results = get_substring_matches(text, collections)
        close_results = difflib.get_close_matches(text, collections)

        total_results = list(dict.fromkeys(substring_results + close_results))
        return {name: collections[name] for name in total_results[:OPTIONS_LIMIT]}

    def collections_sort_key(self, document: outline.Document) -> int:
        if document.collection_id not in COLLECTION_NAMES.keys():
            return -1

        return list(COLLECTION_NAMES.keys()).index(document.collection_id)

    def process_context(self, context: str) -> str:
        """Processes Outline search result context into a Discord-usable version"""

        query_match = re.search(CONTEXT_HIGHLIGHT_PATTERN, context)
        if query_match:
            context = shorten_around(query_match.group(), context, CONTEXT_LIMIT)
            context = re.sub(CONTEXT_HIGHLIGHT_PATTERN, r"*\1*", context)
        else:
            context = shorten(context, CONTEXT_LIMIT)

        context = self.translate_markdown(context)

        return context or "*Empty*"

    async def search_documents(
        self,
        query: str | None,
        collection_id: str | None,
        *,
        offset: Optional[int] = None,
        limit: Optional[int] = OPTIONS_LIMIT,
    ) -> List[SearchResult]:
        """Search documents based on query"""

        query = query.strip().casefold()
        if not query:
            documents = await self.client.list_documents(collection_id=collection_id, offset=offset, limit=limit)
            documents.sort(key=self.collections_sort_key)
            results = [SearchResult(self.process_context(document.text), document) for document in documents]
        else:
            results = await self.client.search_documents(
                query, collection_id=collection_id, offset=offset, limit=limit, ranking_threshold=RANKING_THRESHOLD
            )
            results = [result for result in results]
            results = [SearchResult(self.process_context(result.context), result.document) for result in results]

        return results

    async def find_document(self, query: str | None, collection_id: str | None) -> outline.Document:
        results = await self.search_documents(query, collection_id, limit=1)
        if not results:
            raise NoDocumentsFound

        return results[0].document

    async def paginate_document(self, ctx: GuiduckContext, document: outline.Document):
        if not await has_document_access(ctx, document):
            raise MissingDocumentPermission

        total_lines = len(document.text.split("\n"))
        total_pages = math.ceil(total_lines / LINES_PER_PAGE)
        paginator = Paginator(self.document_to_embed(document), num_pages=total_pages, loop_pages=False)
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
            raise NoDocumentsFound

        if outline.is_valid_uuid(args.text):
            # A specific document id was passed, either manually or via autocomplete
            try:
                doc = await self.client.fetch_document(args.text)
            except outline.NotFound:
                raise NoDocumentsFound

            if args.collection and doc.collection_id != args.collection:
                raise NoDocumentsFound("No documents found in the selected collection.")

            return await self.paginate_document(ctx, doc)

        else:
            collection_id = args.collection
            doc = await self.find_document(args.text, collection_id)
            return await self.paginate_document(ctx, doc)

    async def paginate_search(
        self,
        ctx: GuiduckContext,
        query: str | None,
        collection_id: str | None,
        *,
        ephemeral: Optional[bool] = False,
    ):
        paginator = SearchPaginator(self, query, collection_id, ephemeral=ephemeral)
        await paginator.start(ctx)

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

        await self.paginate_search(ctx, args.text, args.collection, ephemeral=args.ephemeral)

    @document.autocomplete("collection")
    @document_search.autocomplete("collection")
    async def collection_autocomplete(self, interaction: discord.Interaction, current: str):
        ctx = await GuiduckContext.from_interaction(interaction)

        try:
            accessible_collections = await CollectionConverter.get_user_collections(ctx)
        except MissingCommandPermission as e:
            return [app_commands.Choice(name=str(e), value="")]

        if await checks.passes_check(checks.is_admin, ctx):
            accessible_collections["all"] = "all"

        current = current.strip().casefold()
        if not current:
            collections = accessible_collections
        else:
            collections = self.search_collections(current, accessible_collections)
            if not collections:
                return [app_commands.Choice(name=NoCollectionsFound.message, value="")]

        return [app_commands.Choice(name=collection.title(), value=_id) for collection, _id in collections.items()]

    @document.autocomplete("text")
    @document_search.autocomplete("text")
    async def doc_search_autocomplete(self, interaction: discord.Interaction, current: str):
        ctx = await GuiduckContext.from_interaction(interaction)

        try:
            collection_id = await CollectionConverter.convert(ctx, interaction.namespace.collection)
        except (MissingCommandPermission, NoCollectionsFound) as e:
            return [app_commands.Choice(name=str(e), value="")]

        search_results = await self.search_documents(current, collection_id)
        if not search_results:
            return [app_commands.Choice(name=NoDocumentsFound.message, value="")]

        return [
            app_commands.Choice(name=format_document_label(result.document), value=str(result.document.id))
            for result in search_results
        ]


async def setup(bot):
    await bot.add_cog(Outline(bot))
