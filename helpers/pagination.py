import contextlib
from typing import Callable, Dict, List, Optional

import discord
from discord.ext import commands, menus
from discord.utils import maybe_coroutine


class AsyncEmbedCodeBlockTablePageSource(menus.AsyncIteratorPageSource):
    def __init__(
        self,
        data,
        title=None,
        count=None,
        show_index=False,
        format_embed=lambda x: None,
        format_item=str,
    ):
        super().__init__(data, per_page=20)
        self.title = title
        self.show_index = show_index
        self.format_embed = format_embed
        self.format_item = format_item
        self.count = count

    def justify(self, s, width):
        if s.isdigit():
            return s.rjust(width)
        else:
            return s.ljust(width)

    async def format_page(self, menu, entries):
        start = menu.current_page * self.per_page
        table = [
            (f"{i+1}.", *self.format_item(x)) if self.show_index else self.format_item(x)
            for i, x in enumerate(entries, start=menu.current_page * self.per_page)
        ]
        col_lens = [max(len(x) for x in col) for col in zip(*table)]
        lines = ["  ".join(self.justify(x, col_lens[i]) for i, x in enumerate(line)).rstrip() for line in table]
        embed = discord.Embed(
            title=self.title,
            color=discord.Color.blurple(),
            description="```" + f"\n".join(lines) + "```",
        )
        self.format_embed(embed)
        footer = f"Showing entries {start + 1}–{start + len(lines)}"
        if self.count is not None:
            footer += f" out of {self.count}"
        embed.set_footer(text=footer)
        return embed


class EmbedListPageSource(menus.ListPageSource):
    def __init__(self, data, title=None, show_index=False, format_item=str):
        super().__init__(data, per_page=20)
        self.title = title
        self.show_index = show_index
        self.format_item = format_item

    async def format_page(self, menu, entries):
        lines = (
            f"{i+1}. {self.format_item(x)}" if self.show_index else self.format_item(x)
            for i, x in enumerate(entries, start=menu.current_page * self.per_page)
        )
        return discord.Embed(
            title=self.title,
            color=discord.Color.blurple(),
            description=f"\n".join(lines),
        )


class AsyncEmbedListPageSource(menus.AsyncIteratorPageSource):
    def __init__(self, data, title=None, count=None, show_index=False, format_item=str):
        super().__init__(data, per_page=20)
        self.title = title or None
        self.show_index = show_index
        self.format_item = format_item
        self.count = count

    async def format_page(self, menu, entries):
        start = menu.current_page * self.per_page
        lines = [
            f"{i+1}. {self.format_item(x)}" if self.show_index else self.format_item(x)
            for i, x in enumerate(entries, start=start)
        ]
        embed = discord.Embed(
            title=self.title,
            color=discord.Color.blurple(),
            description=f"\n".join(lines),
        )
        footer = f"Showing entries {start + 1}–{start + len(lines)}"
        if self.count is not None:
            footer += f" out of {self.count}"
        embed.set_footer(text=footer)
        return embed


class AsyncEmbedFieldsPageSource(menus.AsyncIteratorPageSource):
    def __init__(self, data, title=None, count=None, format_item=lambda i, x: (i, x)):
        super().__init__(data, per_page=5)
        self.title = title
        self.format_item = format_item
        self.count = count

    async def format_page(self, menu, entries):
        embed = discord.Embed(
            title=self.title,
            color=discord.Color.blurple(),
        )
        start = menu.current_page * self.per_page
        for i, x in enumerate(entries, start=start):
            embed.add_field(**self.format_item(i, x))
        footer = f"Showing entries {start+1}–{i+1}"
        if self.count is not None:
            footer += f" out of {self.count}"
        embed.set_footer(text=footer)
        return embed


FIRST_PAGE_EMOJI = "⏮️"
LAST_PAGE_EMOJI = "⏭️"
PREVIOUS_PAGE_EMOJI = "◀"
NEXT_PAGE_EMOJI = "▶"
STOP_EMOJI = "⏹"
GO_PAGE_EMOJI = "🔢"


PageReturnType = discord.Embed | str
KwargsReturnType = Dict[str, PageReturnType]


class Paginator(discord.ui.View):
    """Simple paginator using buttons.
    Has the following pagination functionalities:
    - first page
    - previous page
    - stop paginator
    - next page
    - last page
    - go to page
    - optional select menu for entry interaction

    Parameters
    ----------
    get_page : Callable[[int], discord.Embed | str]
        The function that takes in the page number and returns what to show for that page.
        It should return either a string or an embed.
    num_pages : int
        The total number of pages. If not provided, disables last button and page looping.
    loop_pages : bool
        If it should allow looping the pages. If False, it disables first/previous and next/last buttons
        if it's at the beginning and end of the pages, respectively.
    timeout_after : int
        After how many seconds of inactivity should the paginator stop

    Methods
    -------
    add_select
        Adds select menu that can allow the user to interact with the entries that are being paginated.
    clear_buttons
        Clears all the pagination buttons from the paginator.
    start
        Starts the paginator.
    """

    def __init__(
        self,
        get_page: Callable[[int], PageReturnType],
        *,
        num_pages: Optional[int] = None,
        loop_pages: Optional[bool] = True,
        timeout_after: Optional[int] = 120,
    ):
        self.get_page = get_page
        self.num_pages = num_pages
        self.loop_pages = loop_pages and self.num_pages is not None

        self.current_page = 0
        self.message = None
        self.ctx = None

        super().__init__(timeout=timeout_after)
        self.pagination_buttons = (self.first, self.previous, self.stop_button, self.next, self.last, self.go)
        self.select = None

    def is_paginating(self) -> bool:
        if self.num_pages is not None:
            return self.num_pages > 1
        return True

    def clear_buttons(self):
        """Clears the pagination buttons"""

        for button in self.pagination_buttons:
            self.remove_item(button)

    def add_select(self, select: discord.ui.Select, get_options: Callable[[int], List[discord.SelectOption]]):
        """
        Adds the provided select menu to the first row of the paginator.
        This select menu can be used to allow the user to interact with the entries that are being paginated.

        Parameters
        ----------
        select : discord.ui.Select
            The select menu to be added.
        get_options : Callable[[int], List[discord.SelectOption]]
            The function (can be async) that will be called every time the page is changed to get the options
            to show on the select menu for that page.
            Current page number should be its sole argument, similar to get_page.
        """

        self.select = select
        self.get_options = get_options

        # Add the select to the first row
        self.clear_items()
        if self.select:
            self.add_item(self.select)

        for button in self.pagination_buttons:
            self.add_item(button)

    def _get_page_kwargs(self, page: PageReturnType) -> KwargsReturnType:
        kwargs = {}

        if isinstance(page, discord.Embed):
            kwargs["embed"] = page
        elif isinstance(page, str):
            kwargs["content"] = page

        return kwargs

    def _update_items(self) -> None:
        pidx = self.current_page
        if not self.is_paginating():
            self.clear_buttons()
            return

        if self.num_pages is None:
            self.last.disabled = True
            self.go.disabled = True
        else:
            self.go.disabled = False

        if not self.loop_pages:
            self.first.disabled = pidx == 0
            self.previous.disabled = pidx == 0

            if self.num_pages is not None:
                self.next.disabled = (pidx + 1) >= self.num_pages
                self.last.disabled = (pidx + 1) >= self.num_pages

    async def _prepare_page(self, pidx: int) -> KwargsReturnType:
        page = await maybe_coroutine(self.get_page, pidx)

        if self.select:
            select_options = await maybe_coroutine(self.get_options, pidx)
            if select_options:
                self.select.options = select_options
                self.select.disabled = False
            else:
                self.select.options = [discord.SelectOption(label="None")]
                self.select.disabled = True

        kwargs = self._get_page_kwargs(page)
        if kwargs:
            self.current_page = pidx
        self._update_items()

        return kwargs

    async def show_page(self, interaction: discord.Interaction, pidx: int) -> None:
        if self.num_pages is not None:
            pidx = pidx % self.num_pages

        if self.current_page == pidx:
            with contextlib.suppress(discord.NotFound, discord.InteractionResponded):
                return await interaction.response.defer()

        kwargs = await self._prepare_page(pidx)
        try:
            await interaction.response.edit_message(**kwargs, view=self)
        except (discord.NotFound, discord.InteractionResponded):
            if self.message:
                await self.message.edit(**kwargs, view=self)

    async def start(self, ctx: commands.Context, pidx: int = 0):
        self.ctx = ctx
        kwargs = await self._prepare_page(pidx)
        self.message = await ctx.reply(**kwargs, view=self, mention_author=False)

    @discord.ui.button(emoji=FIRST_PAGE_EMOJI, style=discord.ButtonStyle.grey)
    async def first(self, interaction: discord.Interaction, button: discord.Button):
        await self.show_page(interaction, 0)

    @discord.ui.button(emoji=PREVIOUS_PAGE_EMOJI, style=discord.ButtonStyle.grey)
    async def previous(self, interaction: discord.Interaction, button: discord.Button):
        await self.show_page(interaction, self.current_page - 1)

    @discord.ui.button(emoji=STOP_EMOJI, style=discord.ButtonStyle.grey)
    async def stop_button(self, interaction: discord.Interaction, button: discord.Button):
        await interaction.response.defer()
        self.stop()
        try:
            await interaction.delete_original_response()
        except (discord.NotFound, discord.InteractionResponded):
            if self.message:
                await self.message.delete()

    @discord.ui.button(emoji=NEXT_PAGE_EMOJI, style=discord.ButtonStyle.grey)
    async def next(self, interaction: discord.Interaction, button: discord.Button):
        await self.show_page(interaction, self.current_page + 1)

    @discord.ui.button(emoji=LAST_PAGE_EMOJI, style=discord.ButtonStyle.grey)
    async def last(self, interaction: discord.Interaction, button: discord.Button):
        await self.show_page(interaction, self.num_pages - 1)

    @discord.ui.button(emoji=GO_PAGE_EMOJI, style=discord.ButtonStyle.grey)
    async def go(self, interaction: discord.Interaction, button: discord.Button):
        await interaction.response.send_message("What page would you like to go to?")
        message = await interaction.client.wait_for(
            "message",
            check=lambda m: m.author == interaction.user and m.channel == interaction.channel,
            timeout=30,
        )
        try:
            pidx = int(message.content) - 1
        except ValueError:
            return await interaction.followup.send("That's not a valid page number!")
        else:
            await self.show_page(interaction, pidx)

        interaction.client.loop.create_task(interaction.delete_original_response())
        interaction.client.loop.create_task(message.delete())

    async def on_timeout(self) -> None:
        if self.message:
            for item in self.children:
                item.disabled = True

            await self.message.edit(view=self)

    async def interaction_check(self, interaction):
        if self.ctx:
            if interaction.user.id != self.ctx.author.id:
                await interaction.response.send_message("You can't use this!", ephemeral=True)
                return False
        return True
