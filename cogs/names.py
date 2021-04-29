import unicodedata

from discord.ext import commands

LAST_RESORT_NICKNAME = "User"
GUILD_ID = 716390832034414685


class Names(commands.Cog):
    """For normalizing usernames."""

    def __init__(self, bot):
        self.bot = bot

    def normalized(self, text):
        if text is None:
            return None
        text = unicodedata.normalize("NFKC", text)
        while len(text) > 0 and text[0] < "0":
            text = text[1:]
        if len(text) == 0:
            return None
        return text[:32]

    async def normalize_member(self, member):
        normalized = (
            self.normalized(member.nick) or self.normalized(member.name) or LAST_RESORT_NICKNAME
        )
        if normalized != member.display_name:
            await member.edit(nick=normalized)

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if before.nick == after.nick:
            return
        await self.normalize_member(after)

    @commands.Cog.listener()
    async def on_user_update(self, before, after):
        if before.name == after.name:
            return
        guild = self.bot.get_guild(GUILD_ID)
        after = guild.get_member(after.id)
        await self.normalize_member(after)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        guild = self.bot.get_guild(GUILD_ID)
        member = guild.get_member(member.id)
        await self.normalize_member(member)


def setup(bot):
    bot.add_cog(Names(bot))
