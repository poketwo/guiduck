import random
from collections import defaultdict

import discord
from discord.ext import commands
from discord.ext.menus.views import ViewMenuPages

from helpers.pagination import AsyncEmbedFieldsPageSource

SILENT = False

ROLES = defaultdict(
    list,
    {
        5: [1089551162421813340],
        10: [1091616925655777290, 1089551264305664123, 1140360854282436659],
        15: [1089551242730160168],
        20: [1091614218379345962],
        25: [1089551687930351626],
        30: [1091614215082610698, 1092276608729108643],
        35: [1089551721388331060],
        40: [1091616293465116737],
        50: [1091615522359087195, 1089551989895090247],
        60: [1091614208573067368],
        70: [1091616290780741698],
        80: [1091615523860647998],
    },
)


class Levels(commands.Cog):
    """For XP and levels."""

    def __init__(self, bot):
        self.bot = bot

    def min_xp_at(self, level):
        return (2 * level * level + 27 * level + 91) * level * 5 // 6

    async def sync_level_roles(self, member):
        user = await self.bot.mongo.db.member.find_one({"_id": {"id": member.id, "guild_id": member.guild.id}})
        if user is None:
            return
        level_role_ids = {x for k, r in ROLES.items() if k <= user.get("level", 0) for x in r}
        role_ids = {x.id for x in member.roles}
        if level_role_ids <= role_ids:
            return
        await member.add_roles(*[discord.Object(x) for x in level_role_ids])

    @commands.Cog.listener()
    async def on_member_join(self, member):
        await self.sync_level_roles(member)

    @commands.Cog.listener()
    async def on_command(self, ctx):
        if ctx.guild:
            await self.sync_level_roles(ctx.author)

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
            new_level = user.get("level", 0) + 1

            roles = [message.guild.get_role(x) for x in ROLES[new_level]]
            await message.author.add_roles(*roles)
            await self.bot.mongo.db.member.update_one(
                {"_id": {"id": message.author.id, "guild_id": message.guild.id}},
                {"$inc": {"level": 1}},
            )

            msg = f"Congratulations {message.author.mention}, you are now level **{new_level}**!"
            for role in roles:
                msg += f" You have received the **{role.mention}** role."

            if not SILENT:
                await message.channel.send(msg)
            if level_logs_channel is not None:
                await level_logs_channel.send(f"{message.author.mention} reached level **{new_level}**.")

    @commands.hybrid_command(aliases=("rank", "level"))
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

    @commands.hybrid_command(aliases=("top", "lb", "levels"))
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
