import asyncio
from collections import defaultdict

import discord
from discord.ext import commands, tasks

from helpers import checks, constants

ROLE_MAPPING_COMMUNITY_TO_SUPPORT = {
    974942298301431809: 930346838589063208,
    718006431231508481: 930346842586218607,
    974937010462683166: 964026958117748816,
    724879492622843944: 930346843521556540,
    813433839471820810: 930346845547409439,
    732712709514199110: 930346847443255306,
    794438698241884200: 930346848529547314,
}

ROLE_MAPPING_SUPPORT_TO_COMMUNITY = {v: k for k, v in ROLE_MAPPING_COMMUNITY_TO_SUPPORT.items()}


class RoleSync(commands.Cog):
    """For syncing roles between the support server and community server."""

    def __init__(self, bot):
        self.bot = bot
        self.sync_all_task.start()
        self.locks = defaultdict(asyncio.Lock)

    @tasks.loop(minutes=20)
    async def sync_all_task(self):
        await self.sync_all()

    @sync_all_task.before_loop
    async def before_sync_all(self):
        return await self.bot.wait_until_ready()

    async def sync_all(self):
        guild = self.bot.get_guild(constants.COMMUNITY_SERVER_ID)
        if not guild:
            return

        for member in guild.members:
            await self.sync_member(member)

    async def sync_member(self, member):
        async with self.locks[member.id]:
            member = member.guild.get_member(member.id)

            if member.guild.id == constants.COMMUNITY_SERVER_ID:
                other_guild = constants.SUPPORT_SERVER_ID
                mapping = ROLE_MAPPING_COMMUNITY_TO_SUPPORT
            elif member.guild.id == constants.SUPPORT_SERVER_ID:
                other_guild = constants.COMMUNITY_SERVER_ID
                mapping = ROLE_MAPPING_SUPPORT_TO_COMMUNITY
            else:
                return

            other_guild = self.bot.get_guild(other_guild)
            other_member = other_guild.get_member(member.id)
            if other_member is None:
                return

            for role, other_role in mapping.items():
                if discord.utils.get(member.roles, id=role) and not discord.utils.get(
                    other_member.roles, id=other_role
                ):
                    await other_member.add_roles(discord.Object(other_role))
                if not discord.utils.get(member.roles, id=role) and discord.utils.get(
                    other_member.roles, id=other_role
                ):
                    await other_member.remove_roles(discord.Object(other_role))

    @commands.Cog.listener(name="on_member_join")
    @commands.Cog.listener(name="on_member_update")
    async def on_member_updates(self, *args):
        thing = args[-1]
        await self.sync_member(thing)

    @commands.hybrid_command(name="sync-roles")
    @commands.guild_only()
    @checks.is_community_manager()
    async def sync_roles(self, ctx):
        """Syncs all roles from the community server to the support server.

        You must have the Community Manager role to use this."""

        await ctx.send("Syncing all roles...")
        await self.sync_all()
        await ctx.send("Completed role sync.")

    async def cog_unload(self):
        self.sync_all_task.cancel()


async def setup(bot):
    await bot.add_cog(RoleSync(bot))
