import asyncio

from discord.ext import commands


class Paginator:
    def __init__(self, get_page, num_pages):
        self.num_pages = num_pages
        self.get_page = get_page

    async def send(self, ctx: commands.Context, pidx: int = 0):

        embed = await self.get_page(pidx)
        message = await ctx.send(embed=embed)

        if self.num_pages <= 1:
            return

        await self.message.add_reaction("⏮️")
        await self.message.add_reaction("◀")
        await self.message.add_reaction("▶")
        await self.message.add_reaction("⏭️")
        await self.message.add_reaction("🔢")
        await self.message.add_reaction("⏹")

        try:
            while True:
                reaction, user = await ctx.bot.wait_for(
                    "reaction_add",
                    check=lambda r, u: r.message.id == self.message.id
                    and u.id == self.author.id,
                    timeout=120,
                )
                try:
                    await reaction.remove(user)
                except:
                    pass

                if reaction.emoji == "⏹":
                    await self.message.delete()
                    return

                elif reaction.emoji == "🔢":
                    ask_message = await ctx.send("What page would you like to go to?")
                    message = await ctx.bot.wait_for(
                        "message",
                        check=lambda m: m.author == self.author
                        and m.channel == ctx.channel,
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
                await self.message.edit(embed=embed)

        except asyncio.TimeoutError:
            await self.message.add_reaction("❌")
