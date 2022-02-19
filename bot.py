import discord
from discord.ext import commands, events
from discord.ext.events import member_kick

import config

COGS = [
    "automod",
    "autopost",
    "bot",
    "collectors",
    "data",
    "forms",
    "help_desk",
    "help",
    "logging",
    "moderation",
    "mongo",
    "names",
    "reaction_roles",
    "redis",
    "reminders",
    "reputation",
    "tags",
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

        self.load_extension("jishaku")
        for i in COGS:
            self.load_extension(f"cogs.{i}")

    @property
    def mongo(self):
        return self.get_cog("Mongo")

    @property
    def redis(self):
        return self.get_cog("Redis").pool

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


if __name__ == "__main__":
    bot = Bot()
    bot.run(config.BOT_TOKEN)
