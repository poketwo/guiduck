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

        - Research trades, market listings, and auctions with similar pokémon from the past.
        - Ask for other trainers' opinions if unsure about the fairness of a trade.
        - Don't give into pressure to buy or sell immediately. You can always try again later.
        - Read through the trading tips document linked in the <#754774504571666534> channel.
        """,
    ),
    AutoPost(
        [741712512113967214, 720331733949743176, 768161635096461362],
        """
        **Reminder:** Check pins for full rules. Advertisements in violation will be removed.
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
            903937573674700811,
            926089209268293693,
            926089216658649119,
            1091625926070108231,
            1091625948320890911,
            1091625991971012680,
            1091626047730089996,
            1091626346951757865,
            1183288943542800456,
            1091626412273831986,
            1183290305089376346,
            1183290734871322684,
            1226514338848706580,
            1235358540592451666,
        ],
        """
        **Reminder:** This channel is for catching only.

        - **All catching channels are Free-For-All**. Pinging users for shiny-hunts and collections is courteous, but nobody is obligated to honor it or return the favor. Please do not make people feel guilty for catching pokémon they want to.
        - Spamming is not allowed here or anywhere else in the server. Also, please be careful of your message speed when catching in multiple channels simultaneously.
        - Do not run generic bot commands here, there are multiple channels for that such as <#953802841175232552> and <#1085305620674129920>.
        - Market and auction advertisements are not allowed. Use channels such as <#741712512113967214> and <#768161635096461362>.
        - Check pinned messages for a comprehensive list of rules. Failure to abide by them may result in a removal of access to catching channels.
        """,
    ),
]


class Autopost(commands.Cog):
    """For autoposting important messages."""

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

    async def cog_unload(self):
        self.autopost.cancel()


async def setup(bot):
    await bot.add_cog(Autopost(bot))
