import discord
from discord.ext import commands

DEBOUNCE_SECONDS = 600


class MemberSeen(commands.Cog):
    """Dispatches `on_member_seen(member, message)` when a member posts or types in a guild.

    Join/update events require the privileged Members intent, so cogs that need to react to a member
    (re-applying mutes after a rejoin, fixing nicknames, syncing roles) listen for this instead. It fires
    at most once per member per DEBOUNCE_SECONDS.
    """

    def __init__(self, bot):
        self.bot = bot

    async def seen(self, member, message=None):
        if not isinstance(member, discord.Member) or member.bot:
            return
        key = f"member_seen:{member.guild.id}:{member.id}"
        if not await self.bot.redis.set(key, 1, expire=DEBOUNCE_SECONDS, exist=self.bot.redis.SET_IF_NOT_EXIST):
            return
        self.bot.dispatch("member_seen", member, message)

    @commands.Cog.listener()
    async def on_message(self, message):
        await self.seen(message.author, message)

    @commands.Cog.listener()
    async def on_typing(self, channel, user, when):
        await self.seen(user)


async def setup(bot):
    await bot.add_cog(MemberSeen(bot))
