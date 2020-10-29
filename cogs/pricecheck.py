import itertools

from discord.ext import commands, tasks

CHANNELS = [
    720040664741576775,
    721778540080791569,
    721846241696284694,
]


class PriceCheck(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.channels = itertools.cycle(CHANNELS)
        self.updated = {x: False for x in CHANNELS}

        self.pc_reminder.start()

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author == self.bot.user:
            return
        self.updated[message.channel.id] = True

    @tasks.loop(minutes=2)
    async def pc_reminder(self):
        await self.bot.wait_until_ready()
        channel_id = next(self.channels)

        if self.updated[channel_id]:
            channel = self.bot.get_channel(channel_id)
            await channel.send(
                "**Reminder:** When buying or selling Pokémon, please check the value of your Pokémon at <#722244899767844866> to make sure you get the right price for it!"
            )
            self.updated[channel_id] = False

    def cog_unload(self):
        self.pc_reminder.cancel()


def setup(bot):
    bot.add_cog(PriceCheck(bot))
