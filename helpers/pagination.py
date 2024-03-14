import asyncio
from typing import Optional

import discord
from discord.ext import commands, menus


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


class Paginator(discord.ui.View):
    def __init__(self, get_page, num_pages: int, *, loop: Optional[bool] = True):
        self.num_pages = num_pages
        self.get_page = get_page
        self.loop = loop

        self.current_page = 0
        self.message = None
        super().__init__(timeout=120)

    def is_paginating(self) -> bool:
        return self.num_pages > 1

    def _update_labels(self) -> None:
        if not self.is_paginating():
            self.clear_items()
            return

        if not self.loop:
            pidx = self.current_page
            self.first.disabled = pidx == 0
            self.last.disabled = (pidx + 1) >= self.num_pages
            self.next.disabled = (pidx + 1) >= self.num_pages
            self.previous.disabled = pidx == 0

    async def show_page(self, interaction: discord.Interaction, pidx: int) -> None:
        if self.current_page == pidx:
            try:
                return await interaction.response.defer()
            except (discord.NotFound, discord.InteractionResponded):
                pass

        embed = await discord.utils.maybe_coroutine(self.get_page, pidx)
        self.current_page = pidx
        self._update_labels()

        try:
            await interaction.response.edit_message(embed=embed, view=self)
        except (discord.NotFound, discord.InteractionResponded):
            if self.message:
                await self.message.edit(embed=embed, view=self)

    async def start(self, ctx: commands.Context, pidx: int = 0):
        self._update_labels()
        embed = await discord.utils.maybe_coroutine(self.get_page, pidx)
        self.message = await ctx.reply(embed=embed, view=self, mention_author=False)

    @discord.ui.button(emoji=FIRST_PAGE_EMOJI, style=discord.ButtonStyle.grey)
    async def first(self, interaction: discord.Interaction, button: discord.Button):
        await self.show_page(interaction, 0)

    @discord.ui.button(emoji=PREVIOUS_PAGE_EMOJI, style=discord.ButtonStyle.grey)
    async def previous(self, interaction: discord.Interaction, button: discord.Button):
        await self.show_page(interaction, (self.current_page - 1) % self.num_pages)

    @discord.ui.button(emoji=STOP_EMOJI, style=discord.ButtonStyle.grey)
    async def stop(self, interaction: discord.Interaction, button: discord.Button):
        await interaction.response.defer()
        try:
            await interaction.delete_original_response()
        except (discord.NotFound, discord.InteractionResponded):
            if self.message:
                await self.message.delete()

    @discord.ui.button(emoji=NEXT_PAGE_EMOJI, style=discord.ButtonStyle.grey)
    async def next(self, interaction: discord.Interaction, button: discord.Button):
        await self.show_page(interaction, (self.current_page + 1) % self.num_pages)

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
            pidx = (int(message.content) - 1) % self.num_pages
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
