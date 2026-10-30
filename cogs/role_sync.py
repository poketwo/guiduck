import asyncio
from collections import defaultdict

import discord
from discord.ext import commands, tasks

from helpers import checks, constants
from helpers.utils import get_or_fetch_member

ROLE_MAPPING_COMMUNITY_TO_SUPPORT = {
    974942298301431809: 930346838589063208,
    718006431231508481: 930346842586218607,
    1219500643731898450: 1260660223618580572,
    1219500880534179892: 1260660172561322005,
    1219501382437048402: 1260660162620821547,
    1219501453240959006: 1260660160708214907,
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

        if guild.chunked:
            for member in guild.members:
                await self.sync_member(member)
            return

        for member_id in await self.find_unsynced_member_ids():
            if member := await get_or_fetch_member(guild, member_id):
                await self.sync_member(member)

    async def find_unsynced_member_ids(self):
        """Compares cached roles in the `member` collection, so only out-of-sync members need fetching."""

        role_ids = [*ROLE_MAPPING_COMMUNITY_TO_SUPPORT, *ROLE_MAPPING_SUPPORT_TO_COMMUNITY]
        guild_ids = [constants.COMMUNITY_SERVER_ID, constants.SUPPORT_SERVER_ID]
        roles = defaultdict(dict)

        async for x in self.bot.mongo.db.member.find({"_id.guild_id": {"$in": guild_ids}, "roles": {"$in": role_ids}}):
            roles[x["_id"]["id"]][x["_id"]["guild_id"]] = x.get("roles", [])

        missing = [
            {"id": member_id, "guild_id": constants.SUPPORT_SERVER_ID}
            for member_id, guild_roles in roles.items()
            if constants.SUPPORT_SERVER_ID not in guild_roles
        ]
        async for x in self.bot.mongo.db.member.find({"_id": {"$in": missing}}):
            roles[x["_id"]["id"]][x["_id"]["guild_id"]] = x.get("roles", [])

        return [
            member_id
            for member_id, guild_roles in roles.items()
            if constants.SUPPORT_SERVER_ID in guild_roles and not self.is_synced(guild_roles)
        ]

    def is_synced(self, guild_roles):
        community = {
            ROLE_MAPPING_COMMUNITY_TO_SUPPORT[x]
            for x in guild_roles.get(constants.COMMUNITY_SERVER_ID, [])
            if x in ROLE_MAPPING_COMMUNITY_TO_SUPPORT
        }
        support = {
            x for x in guild_roles.get(constants.SUPPORT_SERVER_ID, []) if x in ROLE_MAPPING_SUPPORT_TO_COMMUNITY
        }
        return community == support

    async def sync_member(self, member):
        async with self.locks[member.id]:
            member = member.guild.get_member(member.id) or member

            if member.guild.id == constants.COMMUNITY_SERVER_ID:
                other_guild = constants.SUPPORT_SERVER_ID
                mapping = ROLE_MAPPING_COMMUNITY_TO_SUPPORT
            elif member.guild.id == constants.SUPPORT_SERVER_ID:
                other_guild = constants.COMMUNITY_SERVER_ID
                mapping = ROLE_MAPPING_SUPPORT_TO_COMMUNITY
            else:
                return

            other_guild = self.bot.get_guild(other_guild)
            other_member = await get_or_fetch_member(other_guild, member.id)
            if other_member is None:
                return

            other_role_ids = {x.id for x in other_member.roles}
            for role, other_role in mapping.items():
                if discord.utils.get(member.roles, id=role) and not discord.utils.get(
                    other_member.roles, id=other_role
                ):
                    await other_member.add_roles(discord.Object(other_role))
                    other_role_ids.add(other_role)
                if not discord.utils.get(member.roles, id=role) and discord.utils.get(
                    other_member.roles, id=other_role
                ):
                    await other_member.remove_roles(discord.Object(other_role))
                    other_role_ids.discard(other_role)

            if not self.bot.intents.members:
                await self.cache_roles(member, {x.id for x in member.roles})
                await self.cache_roles(other_member, other_role_ids)

    async def cache_roles(self, member, role_ids):
        """Keeps cached roles current so `find_unsynced_member_ids` doesn't refetch already-synced members."""

        await self.bot.mongo.db.member.update_one(
            {"_id": {"id": member.id, "guild_id": member.guild.id}}, {"$set": {"roles": list(role_ids)}}
        )

    @commands.Cog.listener(name="on_member_join")
    @commands.Cog.listener(name="on_member_update")
    async def on_member_updates(self, *args):
        thing = args[-1]
        await self.sync_member(thing)

    @commands.Cog.listener()
    async def on_member_seen(self, member, message):
        """Syncs active members, since member update events need the Members intent."""

        if self.bot.intents.members:
            return
        if member.guild.id == constants.COMMUNITY_SERVER_ID:
            other_guild_id = constants.SUPPORT_SERVER_ID
        elif member.guild.id == constants.SUPPORT_SERVER_ID:
            other_guild_id = constants.COMMUNITY_SERVER_ID
        else:
            return

        other = await self.bot.mongo.db.member.find_one(
            {"_id": {"id": member.id, "guild_id": other_guild_id}}, {"roles": 1}
        )
        if other is None:
            return
        guild_roles = {member.guild.id: [x.id for x in member.roles], other_guild_id: other.get("roles", [])}
        if self.is_synced(guild_roles):
            return

        if member.guild.id != constants.COMMUNITY_SERVER_ID:
            member = await get_or_fetch_member(self.bot.get_guild(constants.COMMUNITY_SERVER_ID), member.id)
        if member is not None:
            await self.sync_member(member)

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
