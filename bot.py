import aiohttp
import discord
from discord.ext import commands, events
from discord.ext.events import member_kick

import config
from helpers.context import GuiduckContext

ESSENTIAL_COGS = [
    "bot",
    "data",
    "help",
    "mongo",
    "logging",
    "redis",
]

COGS = [
    "automod",
    "autopost",
    "auto_lock_threads",
    "collectors",
    "forms",
    "giveaways",
    "help_desk",
    "levels",
    "moderation",
    "names",
    "poketwo_administration",
    "reaction_roles",
    "reminders",
    "reputation",
    "role_sync",
    "tags",
    "outline",
]


class Bot(commands.Bot, events.EventsMixin):
    def __init__(self, **kwargs):
        super().__init__(
            **kwargs,
            command_prefix=config.PREFIX,
            intents=discord.Intents.all(),
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False),
            case_insensitive=True,
        )

        self.config = config

    async def is_owner(self, user):
        if isinstance(user, discord.Member):
            if any(x.id in (718006431231508481, 930346842586218607, 1120600250474827856) for x in user.roles):
                return True
        return await super().is_owner(user)

    async def _async_setup_hook(self):
        await super()._async_setup_hook()
        self.http.connector = aiohttp.TCPConnector(limit=0)

    async def setup_hook(self):
        self.http_session = aiohttp.ClientSession()
        await self.load_extension("jishaku")
        for i in ESSENTIAL_COGS:
            await self.load_extension(f"cogs.{i}")
        for i in COGS:
            await self.load_extension(f"cogs.{i}")

    @property
    def mongo(self):
        return self.get_cog("Mongo")

    @property
    def redis(self):
        return self.get_cog("Redis").pool

    @property
    def poketwo_redis(self):
        return self.get_cog("Redis").poketwo_pool

    @property
    def log(self):
        return self.get_cog("Logging").log

    @property
    def data(self):
        return self.get_cog("Data").instance

    async def on_ready(self):
        self.log.info(f"Ready called.")

    async def close(self):
        self.log.info("Shutting down")
        await super().close()

    async def get_context(self, origin, /, *, cls=GuiduckContext):
        return await super().get_context(origin, cls=cls)


if __name__ == "__main__":
    bot = Bot()
    bot.run(config.BOT_TOKEN)
