from __future__ import annotations

from dataclasses import dataclass
import math
from textwrap import shorten
from typing import TYPE_CHECKING, List, Optional

import discord

from config import OUTLINE_BASE_URL
from helpers.outline.checks import do_ephemeral, has_document_access
from helpers.outline.constants import COLLECTION_NAMES, CONTEXT_LIMIT, OPTIONS_LIMIT, RESULTS_PER_PAGE
from helpers.outline.exceptions import NoDocumentsFound
from helpers.pagination import Paginator
import outline_api_wrapper as outline


if TYPE_CHECKING:
    from cogs.outline import Outline


@dataclass
class SearchResult:
    context: str
    document: outline.Document


def format_document_label(document: outline.Document) -> str:
    return shorten(document.title, CONTEXT_LIMIT)


class DocumentSelect(discord.ui.Select):
    def __init__(self, paginator: SearchPaginator):
        self.paginator = paginator
        self.cog = self.paginator.cog
        self.client = self.cog.client
        super().__init__(placeholder="View a Document")

    async def get_options(self, pidx: Optional[int] = 0) -> List[discord.SelectOption]:
        paginator = self.paginator
        start = pidx * paginator.per_page
        end = start + paginator.per_page

        options = []
        results = await paginator._fetch_results(start, end)
        for i, result in enumerate(results, start=start + 1):
            label = f"{i}. {format_document_label(result.document)}"
            options.append(
                discord.SelectOption(
                    label=label,
                    value=str(result.document.id),
                    description=result.context,
                )
            )

        return options

    async def callback(self, interaction: discord.Interaction):
        ctx = self.paginator.ctx

        ephemeral = await do_ephemeral(ctx) or self.paginator.ephemeral
        await interaction.response.defer(thinking=True, ephemeral=ephemeral)

        # ಠ_ಠ after hours of head scratching on how to ephemeral the pagination like the others
        # without changing a bunch of things, this is the only solution i could think of
        ctx.interaction = interaction

        selected = self.values[0]
        document = await self.client.fetch_document(selected)
        return await self.cog.paginate_document(ctx, document)


class SearchPaginator(Paginator):
    def __init__(
        self,
        outline_cog: Outline,
        query: str | None,
        collection_id: str | None,
        *,
        ephemeral: Optional[bool] = False,
        per_page: Optional[int] = RESULTS_PER_PAGE,
    ):
        self.cog = outline_cog
        self.query = query
        self.collection_id = collection_id

        self.ephemeral = ephemeral
        self.per_page = per_page

        self._results_cache: List[SearchResult] = []

        super().__init__(self.get_page, timeout_after=300)

        select = DocumentSelect(self)
        self.add_select(select, select.get_options)

    async def _fetch_results(self, start: int, end: int) -> List[SearchResult]:
        """
        Returns results in the provided range (fetches if not in cache) and prepares results
        for the next pages. It also sets self.num_pages if it doesn't fetch enough results.
        """

        # If results for current or next page not in cache, fetch and cache 25 results
        current_results = self._results_cache[start:end]
        next_results = self._results_cache[start + self.per_page : end + self.per_page]
        if (not current_results or not next_results) and self.num_pages is None:
            fetched_results = await self.cog.search_documents(
                self.query, self.collection_id, offset=start / self.per_page, limit=OPTIONS_LIMIT
            )
            for result in fetched_results:
                if await has_document_access(self.ctx, result.document):
                    self._results_cache.append(result)

            # If number of fetched docs is less than limit, it means we found the total num of pages
            if len(fetched_results) < OPTIONS_LIMIT:
                self.num_pages = math.ceil(len(self._results_cache) / self.per_page)

        return self._results_cache[start:end]

    async def get_page(self, pidx: Optional[int] = 0) -> discord.Embed | None:
        start = pidx * self.per_page
        end = start + self.per_page

        results = await self._fetch_results(start, end)
        if not results:
            raise NoDocumentsFound

        embed = discord.Embed(
            title=f'Document Search Results For "{self.query}"' if self.query else "List of Documents",
            color=discord.Color.blurple(),
        )

        footer = f"Showing results {start+1}–{start+len(results)}"
        if self.num_pages is not None:
            footer += f" out of {len(self._results_cache)}"
        embed.set_footer(text=footer)

        for i, result in enumerate(results, start=start + 1):
            timestamp = discord.utils.format_dt(result.document.created_at)

            label = f"{i}. {format_document_label(result.document)}"
            collection = COLLECTION_NAMES.get(result.document.collection_id, "")
            values = (f"*{collection.title()}* \u200c • \u200c [Jump]({result.document.full_url(OUTLINE_BASE_URL)})", f">>> {result.context}")

            embed.add_field(
                name=label,
                value="\n".join(values),
                inline=False,
            )

        return embed
