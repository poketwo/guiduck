from datetime import datetime, timezone
from enum import Enum
from typing import NamedTuple, Optional

import discord
from discord.ext import commands

from helpers import checks

CHAR_LIMIT = 75


class Status(Enum):
    AFK = "AFK"
    ONLINE = "Online"
    DND = "Do Not Disturb"


class MemberStatus(NamedTuple):
    status: Status
    reason: str
    since: int


class Afk(commands.Cog):
    """For AFK status"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def get_status(self, member: discord.Member) -> MemberStatus:
        member_data = await self.bot.mongo.db.member.find_one(
            {"_id": {"id": member.id, "guild_id": member.guild.id}}
        )

        if not member_data:
            return MemberStatus(Status.ONLINE, Status.ONLINE.value, 0)

        return MemberStatus(
            status=Status(member_data.get("status", Status.ONLINE.value)),
            reason=member_data.get("reason", Status.ONLINE.value),
            since=member_data.get("since", 0),
        )

    async def set_status(self, member_id: int, guild_id: int, status: Status, reason: str) -> None:
        await self.bot.mongo.db.member.update_one(
            {"_id": {"id": member_id, "guild_id": guild_id}},
            {"$set": {"status": status.value, "reason": reason, "since": int(datetime.now(timezone.utc).timestamp())}},
            upsert=True,
        )

    async def clear_status(self, member_id: int, guild_id: int) -> None:
        await self.bot.mongo.db.member.update_one(
            {"_id": {"id": member_id, "guild_id": guild_id}},
            {"$set": {"status": Status.ONLINE.value, "reason": Status.ONLINE.value, "since": 0}},
            upsert=True,
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot:
            return
        ctx = await self.bot.get_context(message)
        if ctx.command is not None:
            return

        info = await self.get_status(message.author)
        if info.status == Status.AFK:
            await self.clear_status(message.author.id, message.guild.id)
            await message.channel.send(f"Welcome back **{message.author.name}**, your AFK status has been removed.")

        if not message.mentions:
            return

        for member in set(message.mentions):
            info = await self.get_status(member)
            if info.status == Status.AFK:
                await message.channel.send(
                    f"User **{member.name}** is currently `{info.reason}` since <t:{info.since}:R>."
                )
                if message.channel.permissions_for(member).view_channel:
                    try:
                        await member.send(
                            f"You have been mentioned in {message.channel} while you were {info.reason}.\n"
                            f"Jump To: {message.jump_url}"
                        )
                    except discord.Forbidden:
                        pass
            elif info.status == Status.DND:
                await message.channel.send(
                    f"User **{member.name}** is currently `{info.reason}` and on **Do Not Disturb**."
                )

    @commands.hybrid_group(fallback="set")
    @commands.guild_only()
    async def afk(self, ctx: commands.Context, *, reason: Optional[str] = Status.AFK.value):
        """If no subcommand is called, set your status to AFK"""

        if len(reason) > CHAR_LIMIT:
            return await ctx.send(f"Reason too long (max {CHAR_LIMIT} characters).")

        await self.set_status(ctx.author.id, ctx.guild.id, Status.AFK, reason)
        await ctx.send(f"Set user **{ctx.author.name}** status to `{reason}`.")

    @afk.command()
    @commands.guild_only()
    async def dnd(self, ctx: commands.Context, *, reason: Optional[str] = Status.DND.value):
        """Set your status on Do Not Disturb"""

        if len(reason) > CHAR_LIMIT:
            return await ctx.send(f"Reason too long (max {CHAR_LIMIT} characters).")

        await self.set_status(ctx.author.id, ctx.guild.id, Status.DND, reason)
        await ctx.send(f"Set user **{ctx.author.name}** status to `{reason}` and **Do Not Disturb**.")

    @afk.command()
    @commands.guild_only()
    async def clear(self, ctx: commands.Context):
        """Reset your status"""

        await self.clear_status(ctx.author.id, ctx.guild.id)
        await ctx.send(f"Status cleared for user **{ctx.author.name}**.")

    @afk.command(aliases=("fr",))
    @commands.guild_only()
    @checks.is_server_admin()
    async def forcereset(self, ctx: commands.Context, member: discord.Member, *, note: Optional[str] = None):
        """Resets user's status.

        You must have the Community Manager role to use this."""

        await self.clear_status(member.id, ctx.guild.id)
        await ctx.send(f"Status force cleared for user **{member.name}**.")

        if note is not None:
            try:
                await member.send(f"Your status has been reset because {note}")
            except discord.Forbidden:
                pass

    @afk.command()
    @commands.guild_only()
    async def status(self, ctx: commands.Context, *, member: Optional[discord.Member] = None):
        """Shows your current status."""

        if member is None:
            member = ctx.author
        info = await self.get_status(member)

        embed = discord.Embed(color=discord.Color.blurple())
        embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        if info.status != Status.ONLINE:
            embed.add_field(name=info.status.value, value=f"{info.reason} since <t:{info.since}:R>")
        else:
            embed.add_field(name=info.status.value, value="Online")

        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Afk(bot))
