from datetime import timezone

import discord
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
        self.poketwo_client = AsyncIOMotorClient(bot.config.POKETWO_DATABASE_URI, io_loop=bot.loop)
        self.poketwo_db = self.client[bot.config.POKETWO_DATABASE_NAME]

    async def reserve_id(self, name, reserve=1):
        result = await self.db.counter.find_one_and_update({"_id": name}, {"$inc": {"next": reserve}}, upsert=True)
        if result is None:
            return 0
        return result["next"]

    async def fetch_next_idx(self, member: discord.Member, reserve=1):
        result = await self.poketwo_db.member.find_one_and_update(
            {"_id": member.id},
            {"$inc": {"next_idx": reserve}},
            projection={"next_idx": 1},
        )
        await self.bot.poketwo_redis.hdel(f"db:member", member.id)
        return result["next_idx"]


async def setup(bot):
    await bot.add_cog(Mongo(bot))
