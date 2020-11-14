from discord.ext import commands


class Automod(commands.Cog):
    """For moderation."""

    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
        message.content

    @commands.group(invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def blacklist(self, ctx):
        pass


def setup(bot):
    bot.add_cog(Automod(bot))
