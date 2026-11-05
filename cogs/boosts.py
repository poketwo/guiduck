import discord
from discord.ext import commands

from helpers import constants


class Boosts(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def remove_booster_only_roles(self, member: discord.Member):
        roles = [role for role in member.roles if role.id in constants.BOOSTER_ONLY_ROLES]
        if roles:
            await member.remove_roles(*roles, reason="No longer boosting")

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if before.guild.id != constants.COMMUNITY_SERVER_ID:
            return

        had_booster_role = discord.utils.get(before.roles, id=constants.SERVER_BOOSTER_ROLE)
        has_booster_role = discord.utils.get(after.roles, id=constants.SERVER_BOOSTER_ROLE)

        if had_booster_role and not has_booster_role:
            await self.remove_booster_only_roles(after)

    @commands.Cog.listener()
    async def on_member_seen(self, member, message):
        """Removes booster-only roles from ex-boosters, in case member update events are missed."""

        if member.guild.id != constants.COMMUNITY_SERVER_ID:
            return

        if not discord.utils.get(member.roles, id=constants.SERVER_BOOSTER_ROLE):
            await self.remove_booster_only_roles(member)


async def setup(bot):
    await bot.add_cog(Boosts(bot))
