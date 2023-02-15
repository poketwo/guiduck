from datetime import timedelta

import discord
from discord.ext import commands
from discord.ext.menus.views import ViewMenuPages

from helpers import checks, time
from helpers.pagination import AsyncEmbedCodeBlockTablePageSource

GIVEREP_TRIGGERS = [
    "+rep",
    "thanks",
    "thank",
    "thx",
    "ty",
    "thnx",
    "tnx",
    "tysm",
    "tyvm",
    "thanx",
]


class Reputation(commands.Cog):
    """For rep."""

    def __init__(self, bot):
        self.bot = bot

    async def get_rep(self, member):
        member = await self.bot.mongo.db.member.find_one({"_id": {"id": member.id, "guild_id": member.guild.id}})
        rep = member.get("reputation", 0)
        rank = await self.bot.mongo.db.member.count_documents(
            {"reputation": {"$gt": rep}, "_id.id": {"$ne": member.id}}
        )
        return rep, rank

    async def update_rep(self, member, set=None, inc=None):
        if set is None:
            update = {"$inc": {"reputation": inc}}
        elif inc is None:
            update = {"$set": {"reputation": set}}
        else:
            raise ValueError("Cannot both set and inc")

        await self.bot.mongo.db.member.update_one({"_id": {"id": member.id, "guild_id": member.guild.id}}, update)

    async def process_giverep(self, ctx, member):
        if member == ctx.author:
            return "You can't give rep to yourself!"

        cd = await self.bot.redis.pttl(key := f"rep:{ctx.author.id}")
        if cd >= 0:
            return f"You're on cooldown! Try again in **{time.human_timedelta(timedelta(seconds=cd / 1000))}**."

        user_cd = await self.bot.redis.pttl(user_key := f"rep:{ctx.author.id}:{member.id}")
        if user_cd >= 0:
            return f"You can rep **{member}** again in **{time.human_timedelta(timedelta(seconds=user_cd / 1000))}**."

        await self.bot.redis.set(key, 1, expire=120)
        await self.bot.redis.set(user_key, 1, expire=3600)
        await self.update_rep(member, inc=1)
        await ctx.send(f"Gave 1 rep to **{member}**.")

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or len(message.mentions) == 0:
            return

        words = message.content.casefold().split()
        if any(x in words for x in GIVEREP_TRIGGERS):
            ctx = await self.bot.get_context(message)
            await self.process_giverep(ctx, message.mentions[0])

    @commands.command()
    async def rep(self, ctx, *, member: discord.Member = None):
        """Shows the reputation of a given user."""

        if member is None:
            member = ctx.author

        rep, rank = await self.get_rep(member)
        embed = discord.Embed(color=discord.Color.blurple())
        embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        embed.add_field(name="Reputation", value=str(rep))
        embed.add_field(name="Rank", value=str(rank + 1))
        await ctx.send(embed=embed)

    @commands.command(aliases=("gr", "+"), cooldown_after_parsing=True)
    @commands.cooldown(1, 120, commands.BucketType.user)
    async def giverep(self, ctx, *, member: discord.Member):
        """Gives a reputation point to a user.

        You can only give reputation once every two minutes; once every hour for a specific user."""

        if msg := await self.process_giverep(ctx, member):
            await ctx.send(msg)

    @commands.command()
    @checks.is_community_manager()
    async def setrep(self, ctx, member: discord.Member, value: int):
        """Sets a user's reputation to a given value.

        You must have the Community Manager role to use this."""

        await self.update_rep(member, set=value)
        await ctx.send(f"Set **{member}**'s rep to **{value}**")

    @commands.command()
    async def toprep(self, ctx):
        """Displays the server reputation leaderboard."""

        users = self.bot.mongo.db.member.find(
            {"_id.guild_id": ctx.guild.id, "reputation": {"$gt": 0}},
        ).sort("reputation", -1)
        count = await self.bot.mongo.db.member.count_documents({"_id.guild_id": ctx.guild.id, "reputation": {"$gt": 0}})

        def format_embed(e):
            e.description += (
                f"\nUse `{ctx.prefix}rep` to view your reputation, and `{ctx.prefix}giverep` to give rep to others."
            )

        def format_item(x):
            name = f"{x['name']}#{x['discriminator']}"
            return f"{x.get('reputation', 0)}", "-", name

        pages = ViewMenuPages(
            source=AsyncEmbedCodeBlockTablePageSource(
                users,
                title=f"Reputation Leaderboard",
                format_embed=format_embed,
                format_item=format_item,
                count=count,
                show_index=True,
            )
        )
        await pages.start(ctx)


async def setup(bot):
    await bot.add_cog(Reputation(bot))
