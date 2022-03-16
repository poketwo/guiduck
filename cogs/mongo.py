from datetime import timezone

from bson.codec_options import CodecOptions
from discord.ext import commands
from motor.motor_asyncio import AsyncIOMotorClient


class Mongo(commands.Cog):
    """For database operations."""

    def __init__(self, bot):
        self.bot = bot
        self.client = AsyncIOMotorClient(bot.config.DATABASE_URI, io_loop=bot.loop)
        self.db = self.client[bot.config.DATABASE_NAME].with_options(
            codec_options=CodecOptions(tz_aware=True, tzinfo=timezone.utc)
        )

    async def reserve_id(self, name, reserve=1):
        result = await self.db.counter.find_one_and_update({"_id": name}, {"$inc": {"next": reserve}}, upsert=True)
        if result is None:
            return 0
        return result["next"]


async def setup(bot):
    await bot.add_cog(Mongo(bot))
