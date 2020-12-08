import itertools

from discord.ext import commands, tasks

CHANNELS = [
    720040664741576775,
    721778540080791569,
    721846241696284694,
    778139533945602078,
]

REMINDER_MESSAGE = """
**Reminder:** It is your own responsibility to evaluate trades you make. Once both parties have agreed to and completed a trade, that trade is final. If you change your mind after you make a trade, nothing can nor will be done.

To ensure you get a fair deal, you are highly encouraged to do the following:

• Price all pokémon in the #price-check channel.
• Research trades and auctions with similar pokémon from the past.
• Ask for other trainers' opinions if unsure about the fairness of trade.
• Don't give into pressure to buy or sell immediately. You can always try again later.
""".strip()


class PriceCheck(commands.Cog):
    """For price checking."""

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

    @tasks.loop(seconds=30)
    async def pc_reminder(self):
        await self.bot.wait_until_ready()
        channel_id = next(self.channels)

        if self.updated[channel_id]:
            channel = self.bot.get_channel(channel_id)
            await channel.send(REMINDER_MESSAGE)
            self.updated[channel_id] = False

    def cog_unload(self):
        self.pc_reminder.cancel()


def setup(bot):
    bot.add_cog(PriceCheck(bot))
