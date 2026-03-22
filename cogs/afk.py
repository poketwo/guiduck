import contextlib
import itertools
from datetime import datetime
from typing import Optional

import discord
from discord.ext import commands
from discord.ext.menus.views import ViewMenuPages

from helpers.pagination import AsyncEmbedFieldsPageSource
from helpers import checks

class Afk(commands.Cog):
    """For AFK status"""
    
    def __init__(self, bot):
        self.bot = bot
       
    async def get_status(self, member):
        member_data = await self.bot.mongo.db.member.find_one(
            {"_id": {"id": member.id, "guild_id": member.guild.id}}
        )
        
        if not member_data:
            return False, "Online"
            
        afk = member_data.get("afk", False)
        status = member_data.get("status", "Online")
        return afk, status
        
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.guild is None or message.author.bot:
            return
        ctx = await self.bot.get_context(message)
        if ctx.command is not None:
            return

#        Currently disable may affect Bot's performance      
#        afk, _ = await self.get_status(message.author)
#        if afk:
#            await self.bot.mongo.db.member.find_one_and_update(
#                {"_id": {"id": message.author.id, "guild_id": message.guild.id}},
#                {"$set": {"afk": False, "status": "Online"}},
#                upsert=True,
#            )
#            await message.channel.send(f"Welcome back **{message.author.name}**, your AFK status has been removed.")
        
        if not message.mentions:
            return
       
        for member in set(message.mentions):
            afk, status = await self.get_status(member)
            if afk:
                await message.channel.send(f"User **{member.name}** is currently `{status}`.")
                
                try:
                    await member.send(f"You have been mentioned in {message.channel} while you were {status}.")
                except discord.Forbidden:
                    pass # User unavailable.
    
    @commands.hybrid_command()
    async def afk(self, ctx, *, status: Optional[str] = "AFK"):
        """Set your status to AFK"""
        
        if len(status) > 100:
            return await ctx.send("Status too long (max 100 characters).")
            
        user = await self.bot.mongo.db.member.find_one_and_update(
            {"_id": {"id": ctx.author.id, "guild_id": ctx.guild.id}},
            {"$set": {"afk": True, "status": f"{status} since <t:{int(datetime.utcnow().timestamp())}:R>"}},
            upsert=True,
        )
        await ctx.send(f"Set user **{ctx.author.name}** status to `{status}`.")

    @commands.hybrid_command()
    async def unafk(self, ctx):
        """Reset your status"""
        user = await self.bot.mongo.db.member.find_one_and_update(
            {"_id": {"id": ctx.author.id, "guild_id": ctx.guild.id}},
            {"$set": {"afk": False, "status": "Online"}},
            upsert=True,
        )
        await ctx.send(f"Status cleared for user **{ctx.author.name}**.")

    @commands.hybrid_command()
    @commands.guild_only()
    @checks.is_server_admin()
    async def resetstatus(self, ctx, member: discord.Member):
        """Resets user's status.

        You must have the Community Manager role to use this."""
        
        user = await self.bot.mongo.db.member.find_one_and_update(
            {"_id": {"id": member.id, "guild_id": ctx.guild.id}},
            {"$set": {"afk": False, "status": "Online"}},
            upsert=True,
        )
        await ctx.send(f"Status force cleared for user **{member.name}**.")
 
    @commands.hybrid_command()
    async def status(self, ctx, *, member: Optional[discord.Member] = None):
        """Shows your current status."""
        if member is None:
            member = ctx.author
        afk, status = await self.get_status(member)

        embed = discord.Embed(color=discord.Color.blurple())
        embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        embed.add_field(name="Status", value=f"{status}")

        await ctx.send(embed=embed)
 
async def setup(bot):
    await bot.add_cog(Afk(bot))