import itertools
import textwrap
from collections import defaultdict
from typing import List

from discord.ext import commands, tasks


class AutoPost:
    def __init__(self, channels: List[int], message: str, *, each: int = 1):
        self.do_post = itertools.cycle([True] + [False] * (each - 1))
        self.channels = itertools.cycle(channels)
        self.message = textwrap.dedent(message).strip()


POSTS = [
    AutoPost(
        [720040664741576775, 721778540080791569, 721846241696284694, 778139533945602078],
        """
        **Reminder:** It is your own responsibility to evaluate trades you make. Once both parties have agreed to and completed a trade, that trade is final. If you change your mind after you make a trade, nothing can nor will be done.

        To ensure you get a fair deal, you are highly encouraged to do the following:

        • Price all pokémon in the <#722244899767844866> channel.
        • Research trades and auctions with similar pokémon from the past.
        • Ask for other trainers' opinions if unsure about the fairness of trade.
        • Don't give into pressure to buy or sell immediately. You can always try again later.
        • Read through the trading tips document linked in the <#754774504571666534> channel.
        """,
    ),
    AutoPost(
        [741712512113967214],
        """
        **Reminder:** Please put a price on your <#741712512113967214> listing.
        The price must be a single price that you will sell to other users at. "C/O" is not a thing—use auctions for that.
        Check pins for full rules. Advertisements in violation will be removed.
        """,
        each=6,
    ),
    AutoPost(
        [
            717095398476480562,
            720020140401360917,
            720231680564264971,
            724762012453961810,
            724762035094683718,
        ],
        """
        **Reminder:** This channel is for catching only.

        • Spamming is not allowed here or anywhere else in the server.
        • Do not run generic bot commands here, there are multiple channels for that. <#720029048381767751> and <#784148997593890836>
        • Auctions and market advertisements are not allowed. Use <#741712512113967214> and <#768161635096461362>.
        """,
    ),
]


class PriceCheck(commands.Cog):
    """For price checking."""

    def __init__(self, bot):
        self.bot = bot
        self.posts = POSTS
        self.updated = defaultdict(bool)
        self.autopost.start()

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author == self.bot.user:
            return
        self.updated[message.channel.id] = True

    @tasks.loop(seconds=30)
    async def autopost(self):
        for post in self.posts:
            if not next(post.do_post):
                continue

            channel_id = next(post.channels)
            if self.updated[channel_id]:
                channel = self.bot.get_channel(channel_id)
                await channel.send(post.message)
                self.updated[channel_id] = False

    @autopost.before_loop
    async def before_autopost(self):
        await self.bot.wait_until_ready()

    def cog_unload(self):
        self.autopost.cancel()


def setup(bot):
    bot.add_cog(PriceCheck(bot))
