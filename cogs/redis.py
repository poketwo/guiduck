import aioredis
from discord.ext import commands


class Redis(commands.Cog):
    """For redis."""

    def __init__(self, bot):
        self.bot = bot
        self.pool = None
        self.ready = False
        self._connect_task = self.bot.loop.create_task(self.connect())

    async def connect(self):
        self.pool = await aioredis.create_redis_pool(self.bot.config.REDIS_URI, password=self.bot.config.REDIS_PASSWORD)

    async def close(self):
        self.pool.close()
        await self.pool.wait_closed()

    async def wait_until_ready(self):
        await self._connect_task

    async def cog_unload(self):
        self.bot.loop.create_task(self.close())


async def setup(bot):
    await bot.add_cog(Redis(bot))
