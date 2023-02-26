import random

import discord
from discord.ext import commands
from discord.ext.menus.views import ViewMenuPages

from helpers.pagination import AsyncEmbedFieldsPageSource

SILENT = True


class Levels(commands.Cog):
    """For XP and levels."""

    def __init__(self, bot):
        self.bot = bot

    def min_xp_at(self, level):
        return (2 * level * level + 27 * level + 91) * level * 5 // 6

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.guild is None or message.author.bot:
            return
        ctx = await self.bot.get_context(message)
        if ctx.command is not None:
            return
        if discord.utils.get(message.mentions, id=716390085896962058):
            return

        data = await self.bot.mongo.db.guild.find_one({"_id": message.guild.id})
        try:
            level_logs_channel = self.bot.get_channel(data["level_logs_channel_id"])
        except KeyError:
            return

        # Set 60s timeout between messages
        if await self.bot.redis.get(f"xp:{message.guild.id}:{message.author.id}") is not None:
            return
        await self.bot.redis.set(f"xp:{message.guild.id}:{message.author.id}", 1, expire=60)

        xp = random.randint(15, 25)
        user = await self.bot.mongo.db.member.find_one_and_update(
            {"_id": {"id": message.author.id, "guild_id": message.guild.id}},
            {"$inc": {"messages": 1, "xp": xp}},
            upsert=True,
        )

        if user.get("xp", 0) + xp > self.min_xp_at(user.get("level", 0) + 1):
            await self.bot.mongo.db.member.update_one(
                {"_id": {"id": message.author.id, "guild_id": message.guild.id}},
                {"$inc": {"level": 1}},
            )
            msg = f"Congratulations {message.author.mention}, you are now level **{user.get('level', 0) + 1}**!"
            if not SILENT:
                await message.channel.send(msg)
            if level_logs_channel is not None:
                await level_logs_channel.send(f"{message.author.mention} reached level **{user.get('level', 0) + 1}**.")

    @commands.command(aliases=("rank", "level"))
    async def xp(self, ctx):
        """Shows your server XP and level."""

        user = await self.bot.mongo.db.member.find_one({"_id": {"id": ctx.author.id, "guild_id": ctx.guild.id}})
        rank = await self.bot.mongo.db.member.count_documents(
            {"xp": {"$gt": user.get("xp", 0)}, "_id.id": {"$ne": ctx.author.id}, "_id.guild_id": ctx.guild.id}
        )
        xp, level = user.get("xp", 0), user.get("level", 0)
        progress = xp - self.min_xp_at(level)
        required = self.min_xp_at(level + 1) - self.min_xp_at(level)

        embed = discord.Embed(title=f"Level {level}", color=discord.Color.blurple())
        embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
        embed.add_field(name="XP", value=str(xp))
        embed.add_field(name="Progress", value=f"{progress}/{required}")
        embed.add_field(name="Rank", value=str(rank + 1))
        await ctx.send(embed=embed)

    @commands.command(aliases=("top", "lb", "levels"))
    async def leaderboard(self, ctx):
        """Displays the server XP leaderboard."""

        users = self.bot.mongo.db.member.find({"_id.guild_id": ctx.guild.id}).sort("xp", -1)
        count = await self.bot.mongo.db.member.count_documents({})

        def format_item(i, x):
            name = f"{i + 1}. {x['name']}#{x['discriminator']}"
            if x.get("nick") is not None:
                name = f"{name} ({x['nick']})"
            return {
                "name": name,
                "value": f"{x.get('xp', 0)} (Level {x.get('level', 0)})",
                "inline": False,
            }

        pages = ViewMenuPages(
            source=AsyncEmbedFieldsPageSource(
                users,
                title="XP Leaderboard",
                format_item=format_item,
                count=count,
            )
        )
        await pages.start(ctx)

    if SILENT:
        del xp
        del leaderboard


async def setup(bot):
    await bot.add_cog(Levels(bot))
