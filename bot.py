import discord
from discord import Intents
from discord.ext import commands

import cogs
import config


class Bot(commands.Bot):
    def __init__(self, **kwargs):
        super().__init__(**kwargs, command_prefix="?", intents=discord.Intents.all())

        self.config = config

        self.load_extension("jishaku")
        for i in cogs.default:
            self.load_extension(f"cogs.{i}")

    @property
    def db(self):
        return self.get_cog("Mongo").db

    @property
    def log(self):
        return self.get_cog("Logging").log

    async def on_ready(self):
        self.log.info(f"Ready called.")

    async def close(self):
        self.log.info("Shutting down")
        await super().close()


if __name__ == "__main__":
    bot = Bot()
    bot.run(config.BOT_TOKEN)
