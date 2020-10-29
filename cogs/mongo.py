from discord.ext import commands
from motor.motor_asyncio import AsyncIOMotorClient


class Mongo(commands.Cog):
    """For database operations."""

    def __init__(self, bot):
        self.bot = bot
        self.db = AsyncIOMotorClient(bot.config.DATABASE_URI, io_loop=bot.loop)[
            bot.config.DATABASE_NAME
        ]


def setup(bot):
    bot.add_cog(Mongo(bot))
