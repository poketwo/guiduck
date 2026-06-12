import discord
from discord.ext import commands

from helpers import constants


class Boosts(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if before.guild.id != constants.COMMUNITY_SERVER_ID:
            return

        had_booster_role = discord.utils.get(before.roles, id=constants.SERVER_BOOSTER_ROLE)
        has_booster_role = discord.utils.get(after.roles, id=constants.SERVER_BOOSTER_ROLE)
        hoisted_role = discord.utils.get(after.roles, id=constants.BOOSTER_HOISTED_ROLE)

        if had_booster_role and not has_booster_role and hoisted_role:
            await after.remove_roles(hoisted_role)


async def setup(bot):
    await bot.add_cog(Boosts(bot))
