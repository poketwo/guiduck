import contextlib
import itertools
import random
from collections import defaultdict
from typing import Optional

import discord
from discord.ext import commands
from discord.ext.menus.views import ViewMenuPages
from pymongo import ReturnDocument

from helpers.pagination import AsyncEmbedFieldsPageSource
from helpers import checks

SILENT = False

MIN_XP = 0

ROLES = defaultdict(
    list,
    {
        5: [1089551162421813340],
        10: [1091616925655777290, 1089551264305664123, 1140360854282436659],
        15: [1089551242730160168, 1313926767207252028],
        20: [1091614218379345962],
        25: [1089551687930351626],
        30: [1091614215082610698, 1092276608729108643],
        35: [1089551721388331060],
        40: [1091616293465116737],
        50: [1091615522359087195, 1217154668082233405, 1089551989895090247],
        60: [1091614208573067368, 1183294038259023993],
        70: [1091616290780741698, 1235358246575800431],
        80: [1091615523860647998, 1380030959654535220],
        90: [1183295700889509908, 1380031268598579331],
        100: [1183298921280315472],
    },
)


class Levels(commands.Cog):
    """For XP and levels."""

    def __init__(self, bot):
        self.bot = bot

    def min_xp_at(self, level: int) -> int:
        return (2 * level * level + 27 * level + 91) * level * 5 // 6

    def level_at(self, xp: int) -> int:
        """Returns the highest level reachable with the given amount of XP."""

        level = 0
        while self.min_xp_at(level + 1) <= xp:
            level += 1
        return level

    async def get_level_logs_channel_id(self, guild: discord.Guild) -> int | None:
        """Returns the ID of the guild's configured level logs channel, if any."""

        data = await self.bot.mongo.db.guild.find_one({"_id": guild.id})
        if data is None:
            return None
        return data.get("level_logs_channel_id")

    def roles_between_levels(self, guild: discord.Guild, low: int, high: int) -> list[discord.Role]:
        """Returns the level roles awarded for levels in the (low, high] range."""

        role_ids = itertools.chain(*[roles for level, roles in ROLES.items() if low < level <= high])
        return [role for role in map(guild.get_role, role_ids) if role is not None]

    async def apply_level_roles(
        self, member: discord.Member, old_level: int, new_level: int
    ) -> tuple[list[discord.Role], list[discord.Role]]:
        """Grants and removes level roles to reflect a level change, returning the roles changed."""

        add_roles = self.roles_between_levels(member.guild, old_level, new_level)
        remove_roles = self.roles_between_levels(member.guild, new_level, old_level)
        if add_roles:
            await member.add_roles(*add_roles)
        if remove_roles:
            await member.remove_roles(*remove_roles)
        return add_roles, remove_roles

    def format_roles(self, roles: list[discord.Role]) -> str:
        return ", ".join(f"**{role.mention}**" for role in roles)

    async def sync_level_roles(self, member):
        user = await self.bot.mongo.db.member.find_one({"_id": {"id": member.id, "guild_id": member.guild.id}})
        if user is None:
            return
        level_role_ids = {x for k, r in ROLES.items() if k <= user.get("level", 0) for x in r}
        role_ids = {x.id for x in member.roles}
        if level_role_ids <= role_ids:
            return

        with contextlib.suppress(discord.NotFound):
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

        level_logs_channel_id = await self.get_level_logs_channel_id(message.guild)
        if level_logs_channel_id is None:
            return
        level_logs_channel = self.bot.get_channel(level_logs_channel_id)

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
            u = await self.bot.mongo.db.member.update_one(
                {"_id": {"id": message.author.id, "guild_id": message.guild.id}, "level": user.get("level", None)},
                {"$inc": {"level": 1}},
            )
            if not u.modified_count:
                return

            msg = f"Congratulations {message.author.mention}, you are now level **{new_level}**!"
            for role in roles:
                msg += f" You have received the **{role.mention}** role."

            if not SILENT:
                await message.channel.send(msg)
            if level_logs_channel is not None:
                await level_logs_channel.send(f"{message.author.mention} reached level **{new_level}**.")

    @commands.hybrid_command(aliases=("rank", "level"))
    async def xp(self, ctx, *, member: Optional[discord.Member] = commands.Author):
        """Shows your server XP and level."""

        user = await self.bot.mongo.db.member.find_one({"_id": {"id": member.id, "guild_id": ctx.guild.id}})
        rank = await self.bot.mongo.db.member.count_documents(
            {"xp": {"$gt": user.get("xp", 0)}, "_id.id": {"$ne": member.id}, "_id.guild_id": ctx.guild.id}
        )
        xp, level = user.get("xp", 0), user.get("level", 0)
        progress = xp - self.min_xp_at(level)
        required = self.min_xp_at(level + 1) - self.min_xp_at(level)

        embed = discord.Embed(title=f"Level {level}", color=discord.Color.blurple())
        embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        embed.add_field(name="XP", value=str(xp))
        embed.add_field(name="Progress", value=f"{progress}/{required}")
        embed.add_field(name="Rank", value=str(rank + 1))
        await ctx.send(embed=embed)

    @commands.hybrid_command()
    @commands.guild_only()
    @checks.is_server_admin()
    async def setlevel(self, ctx, member: discord.Member, level: int):
        """Sets a user's level to a given value.

        You must have the Community Manager role to use this."""

        level_logs_channel_id = await self.get_level_logs_channel_id(ctx.guild)
        if level_logs_channel_id is None:
            return await ctx.send("No level logs channel set in this server!")
        level_logs_channel = self.bot.get_channel(level_logs_channel_id)

        await ctx.message.add_reaction("▶️")

        xp = self.min_xp_at(level)
        user = await self.bot.mongo.db.member.find_one_and_update(
            {"_id": {"id": member.id, "guild_id": ctx.guild.id}},
            {"$set": {"xp": xp, "level": level}},
            upsert=True,
        )
        current_level = user.get("level", 0)

        if current_level == level:
            return await ctx.send("No changes made.")

        add_roles, remove_roles = await self.apply_level_roles(member, current_level, level)

        msg = f"Set **{member}**'s level to **{level}**."
        if add_roles:
            msg += f" They have received the roles {self.format_roles(add_roles)}."
        if remove_roles:
            msg += f" The roles {self.format_roles(remove_roles)} have been removed."

        await ctx.channel.send(msg)
        if level_logs_channel is not None:
            await level_logs_channel.send(f"**{member.mention}**'s level has been set to **{level}** by {ctx.author}.")

        await ctx.message.add_reaction("✅")

    @commands.hybrid_command(aliases=("addxp",))
    @commands.guild_only()
    @checks.is_server_admin()
    async def givexp(self, ctx, member: discord.Member, xp: int):
        """Gives a user an amount of XP, updating their level accordingly.

        A negative amount takes XP away instead. You must have the Server Administrator role to use this."""

        if xp == 0:
            return await ctx.send("No changes made.")

        level_logs_channel_id = await self.get_level_logs_channel_id(ctx.guild)
        if level_logs_channel_id is None:
            return await ctx.send("No level logs channel set in this server!")
        level_logs_channel = self.bot.get_channel(level_logs_channel_id)

        await ctx.message.add_reaction("▶️")

        user = await self.bot.mongo.db.member.find_one_and_update(
            {"_id": {"id": member.id, "guild_id": ctx.guild.id}},
            {"$inc": {"xp": xp}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        old_xp, old_level = user["xp"] - xp, user.get("level", 0)
        new_xp = max(user["xp"], MIN_XP)
        new_level = self.level_at(new_xp)

        # Adjust relative to the stored values so concurrent message XP isn't overwritten
        shortfall = new_xp - user["xp"]
        if shortfall:
            await self.bot.mongo.db.member.update_one(
                {"_id": {"id": member.id, "guild_id": ctx.guild.id}}, {"$inc": {"xp": shortfall}}
            )
        if new_level != old_level:
            await self.bot.mongo.db.member.update_one(
                {"_id": {"id": member.id, "guild_id": ctx.guild.id}, "level": user.get("level")},
                {"$set": {"level": new_level}},
            )
        add_roles, remove_roles = await self.apply_level_roles(member, old_level, new_level)

        verb = "Gave" if xp > 0 else "Took"
        msg = f"{verb} **{abs(xp)}** XP {'to' if xp > 0 else 'from'} **{member}** ({old_xp} → {new_xp} XP)."
        if new_level != old_level:
            msg += f" They are now level **{new_level}** (was **{old_level}**)."
        if add_roles:
            msg += f" They have received the roles {self.format_roles(add_roles)}."
        if remove_roles:
            msg += f" The roles {self.format_roles(remove_roles)} have been removed."

        await ctx.channel.send(msg)
        if level_logs_channel is not None:
            await level_logs_channel.send(
                f"**{member.mention}**'s XP has been changed from **{old_xp}** to **{new_xp}**"
                f" (level **{old_level}** → **{new_level}**) by {ctx.author}."
            )

        await ctx.message.add_reaction("✅")

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
