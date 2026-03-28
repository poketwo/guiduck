from datetime import datetime, timezone
from typing import Optional
from enum import Enum

import discord
from discord.ext import commands

from helpers import checks

CHAR_LIMIT = 75

class Status(Enum):
    AFK = "AFK"
    ONLINE = "Online"
    DND = "Do Not Disturb"

class Afk(commands.Cog):
    """For AFK status"""

    def __init__(self, bot):
        self.bot = bot

    async def get_status(self, member):
        member_data = await self.bot.mongo.db.member.find_one(
            {"_id": {"id": member.id, "guild_id": member.guild.id}}
        )

        if not member_data:
            return Status.ONLINE.value, Status.ONLINE.value, 0

        status = member_data.get("status", Status.ONLINE.value)
        reason = member_data.get("reason", Status.ONLINE.value)
        since = member_data.get("since", 0)
        
        return status, reason, since

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.guild is None or message.author.bot:
            return
        if message.content.startswith(tuple(self.bot.command_prefix)):
            return

        status, _, _= await self.get_status(message.author)
        if status == Status.AFK.value:
            await self.bot.mongo.db.member.update_one(
                {"_id": {"id": message.author.id, "guild_id": message.guild.id}},
                {"$set": {"status": Status.ONLINE.value, "reason": Status.ONLINE.value, "since": 0}},
                upsert=True,
            )
            await message.channel.send(f"Welcome back **{message.author.name}**, your AFK status has been removed.")

        if not message.mentions:
            return

        for member in set(message.mentions):
            status, reason, since = await self.get_status(member)
            if status == Status.AFK.value:
                await message.channel.send(f"User **{member.name}** is currently `{reason}` since <t:{since}:R>.")
                
                if message.channel.permissions_for(member).view_channel:
                    try:
                        await member.send(f"You have been mentioned in {message.channel} while you were {reason}.\n Jump To: {message.jump_url}")
                    except discord.Forbidden:
                        pass # User unavailable.
                    
            elif status == Status.DND.value:
                await message.channel.send(f"User **{member.name}** is currently `{reason}` and on **Do Not Disturb**.")

    @commands.hybrid_group(fallback="set")
    @commands.guild_only()
    async def afk(self, ctx, *, reason: Optional[str] = Status.AFK.value):
        """If no subcommand is called, set your status to AFK"""

        if len(reason) > CHAR_LIMIT:
            return await ctx.send(f"Reason too long (max {CHAR_LIMIT} characters).")
        
        await self.bot.mongo.db.member.update_one(
            {"_id": {"id": ctx.author.id, "guild_id": ctx.guild.id}},
            {"$set": {"status": Status.AFK.value, "reason": reason, "since": int(datetime.now(timezone.utc).timestamp())}},
            upsert=True,
        )
        await ctx.send(f"Set user **{ctx.author.name}** status to `{reason}`.")

    @afk.command()
    @commands.guild_only()
    async def dnd(self, ctx, *, reason: Optional[str] = Status.DND.value):
        """Set your status on Do Not Disturb"""
        
        if len(reason) > CHAR_LIMIT:
            return await ctx.send(f"Reason too long (max {CHAR_LIMIT} characters).")
        
        await self.bot.mongo.db.member.update_one(
            {"_id": {"id": ctx.author.id, "guild_id": ctx.guild.id}},
            {"$set": {"status": Status.DND.value, "reason": reason, "since": int(datetime.now(timezone.utc).timestamp())}},
            upsert=True,
        )
        await ctx.send(f"Set user **{ctx.author.name}** status to `{reason}` and **Do Not Disturb**.")

    @afk.command()
    @commands.guild_only()
    async def clear(self, ctx):
        """Reset your status"""
        await self.bot.mongo.db.member.update_one(
            {"_id": {"id": ctx.author.id, "guild_id": ctx.guild.id}},
            {"$set": {"status": Status.ONLINE.value, "reason": Status.ONLINE.value, "since": 0}},
            upsert=True,
        )
        await ctx.send(f"Status cleared for user **{ctx.author.name}**.")

    @afk.command(aliases=("fr",))
    @commands.guild_only()
    @checks.is_server_admin()
    async def forcereset(self, ctx, member: discord.Member, *, note: Optional[str] = None):
        """Resets user's status.

        You must have the Community Manager role to use this."""

        await self.bot.mongo.db.member.update_one(
            {"_id": {"id": member.id, "guild_id": ctx.guild.id}},
            {"$set": {"status": Status.ONLINE.value, "reason": Status.ONLINE.value, "since": 0}},
            upsert=True,
        )
        await ctx.send(f"Status force cleared for user **{member.name}**.")
        
        if note is not None:
            try:
                await member.send(f"Your status has been reset because {note}")
            except discord.Forbidden:
                pass # User unavailable.

    @afk.command()
    @commands.guild_only()
    async def status(self, ctx, *, member: Optional[discord.Member] = None):
        """Shows your current status."""
        if member is None:
            member = ctx.author
        status, reason, since = await self.get_status(member)

        embed = discord.Embed(color=discord.Color.blurple())
        embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        if status != Status.ONLINE.value:
            embed.add_field(name=status, value=f"{reason} since <t:{since}:R>")
        else:
            embed.add_field(name=status, value="Online")
        
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Afk(bot))