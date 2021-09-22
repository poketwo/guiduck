import asyncio

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
        lines = [
            "  ".join(self.justify(x, col_lens[i]) for i, x in enumerate(line)).rstrip()
            for line in table
        ]
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
        self.title = title or discord.Embed.Empty
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
        footer = f"Showing entries {start + 1}–{start + len(lines) + 1}"
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


class Paginator:
    def __init__(self, get_page, num_pages):
        self.num_pages = num_pages
        self.get_page = get_page

    async def send(self, ctx: commands.Context, pidx: int = 0):

        embed = await self.get_page(pidx)
        message = await ctx.send(embed=embed)

        if self.num_pages <= 1:
            return

        await message.add_reaction("⏮️")
        await message.add_reaction("◀")
        await message.add_reaction("▶")
        await message.add_reaction("⏭️")
        await message.add_reaction("🔢")
        await message.add_reaction("⏹")

        try:
            while True:
                reaction, user = await ctx.bot.wait_for(
                    "reaction_add",
                    check=lambda r, u: r.message.id == message.id and u.id == ctx.author.id,
                    timeout=120,
                )
                try:
                    await reaction.remove(user)
                except:
                    pass

                if reaction.emoji == "⏹":
                    await message.delete()
                    return

                elif reaction.emoji == "🔢":
                    ask_message = await ctx.send("What page would you like to go to?")
                    message = await ctx.bot.wait_for(
                        "message",
                        check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                        timeout=30,
                    )
                    try:
                        pidx = (int(message.content) - 1) % self.num_pages
                    except ValueError:
                        await ctx.send("That's not a valid page number!")
                        continue

                    ctx.bot.loop.create_task(ask_message.delete())
                    ctx.bot.loop.create_task(message.delete())

                else:
                    pidx = {
                        "⏮️": 0,
                        "◀": pidx - 1,
                        "▶": pidx + 1,
                        "⏭️": self.num_pages - 1,
                    }[reaction.emoji] % self.num_pages

                embed = await self.get_page(pidx)
                await message.edit(embed=embed)

        except asyncio.TimeoutError:
            await message.add_reaction("❌")
