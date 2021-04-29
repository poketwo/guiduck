from contextlib import suppress
import re
import abc
import json
from datetime import datetime, timedelta
import discord

from discord.ext import commands, menus
from helpers.pagination import AsyncEmbedListPageSource

INVITE_REGEX = r"(?:https?://)?discord(?:app)?\.(?:com/invite|gg)/([a-zA-Z0-9]+)/?"
URL_REGEX = r"(?:https?:)?(?:\/\/)?(?:[^@\n]+@)?(?:www\.)?([^:\/\n]+)"
PUNISHMENTS = {
    5: ("ban", None),
    3: ("mute", timedelta(days=3)),
    2: ("mute", timedelta(hours=2)),
    0: ("warn", None),
}


class AutomodModule(abc.ABC):
    @abc.abstractmethod
    async def check(self, ctx):
        pass


class BannedWords(AutomodModule):
    bucket = "banned_words"

    def __init__(self, bot):
        self.url_regex = re.compile(URL_REGEX)
        self.bot = bot

    async def fetch(self, guild):
        words = await self.bot.redis.get(f"banned_words:{guild.id}")
        if words is None:
            data = await self.bot.mongo.db.guild.find_one({"_id": guild.id})
            words = [] if data is None else data.get("banned_words")
            await self.bot.redis.set(f"banned_words:{guild.id}", json.dumps(words), expire=3600)
        else:
            words = json.loads(words)
        return words

    async def update(self, guild, push=None, pull=None):
        update = {}
        if push is not None:
            update["$push"] = {"banned_words": {"$each": push}}
        if pull is not None:
            update["$pull"] = {"banned_words": {"$in": pull}}
        await self.bot.mongo.db.guild.update_one({"_id": guild.id}, update, upsert=True)
        await self.bot.redis.delete(f"banned_words:{guild.id}")

    async def check(self, ctx):
        banned = set(await self.fetch(ctx.guild))

        words = ctx.message.content.casefold().split()
        word = discord.utils.find(lambda x: x in banned, words)
        if word is not None:
            return f"The word `{word}` is banned, watch your language."

        domains = self.url_regex.findall(ctx.message.content.casefold())
        domain = discord.utils.find(lambda x: x in banned, domains)
        if domain is not None:
            return f"The site `{domain}` is banned, watch your language."


class MassMention(AutomodModule):
    bucket = "mass_mention"

    async def check(self, ctx):
        if len(ctx.message.mentions) >= 10:
            return f"Sending too many mentions."


class ServerInvites(AutomodModule):
    bucket = "server_invites"

    def __init__(self, bot):
        self.regex = re.compile(INVITE_REGEX, flags=re.I)
        self.bot = bot

    async def check(self, ctx):
        for code in self.regex.findall(ctx.message.content):
            with suppress(discord.NotFound):
                invite = await self.bot.fetch_invite(code)
                if invite.guild != ctx.guild:
                    return f"Sending invites to another server."


class Automod(commands.Cog):
    """For moderation."""

    def __init__(self, bot):
        self.bot = bot
        self.banned_words = BannedWords(bot)
        self.modules = [self.banned_words, ServerInvites(bot), MassMention()]

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.guild is None:
            return
        # if message.author.permissions_in(message.channel).manage_messages:
        #     return

        ctx = await self.bot.get_context(message)
        for module in self.modules:
            if reason := await module.check(ctx):
                await self.automod_punish(ctx, module.bucket, reason=reason)

    async def automod_punish(self, ctx, bucket, *, reason):
        await ctx.message.delete()
        cog = self.bot.get_cog("Moderation")
        if cog is None:
            return

        query = {
            "target_id": ctx.author.id,
            "user_id": self.bot.user.id,
            "created_at": {"$gt": datetime.utcnow() - timedelta(weeks=1)},
            "automod_bucket": bucket,
        }
        count = await self.bot.mongo.db.action.count_documents(query) + 1

        kwargs = dict(
            target=ctx.author,
            user=self.bot.user,
            reason=f"Automod: {reason}",
            created_at=datetime.utcnow(),
            automod_bucket=bucket,
        )

        type, duration = next(x for c, x in PUNISHMENTS.items() if count >= c)
        if duration is not None:
            kwargs["expires_at"] = kwargs["created_at"] + duration

        action = cog.cls_dict[type](**kwargs)
        await action.notify()
        await action.execute(ctx)

    @commands.group(invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def automod(self, ctx):
        """Utilities for automoderation."""

        await ctx.send_help(ctx.command)

    @automod.group(invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def words(self, ctx):
        """Displays the banned words list.

        You must have the Administrator permission to use this.
        """

        pages = menus.MenuPages(
            source=AsyncEmbedListPageSource(
                await self.banned_words.fetch(ctx.guild),
                title="Banned Words",
                show_index=True,
            )
        )
        await pages.start(ctx)

    @words.command()
    @commands.has_permissions(administrator=True)
    async def add(self, ctx, *words):
        """Adds words to the banned words list.

        You must have the Administrator permission to use this.
        """

        await self.banned_words.update(ctx.guild, push=[x.casefold() for x in words])
        words_msg = ", ".join(f"**{x}**" for x in words)
        await ctx.send(f"Added {words_msg} to the banned words list.")

    @words.command()
    @commands.has_permissions(administrator=True)
    async def remove(self, ctx, *words):
        """Removes words from the banned words list.

        You must have the Administrator permission to use this.
        """

        await self.banned_words.update(ctx.guild, pull=[x.casefold() for x in words])
        words_msg = ", ".join(f"**{x}**" for x in words)
        await ctx.send(f"Removed {words_msg} from the banned words list.")


def setup(bot):
    bot.add_cog(Automod(bot))