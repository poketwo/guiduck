import abc
import json
import re
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

import discord
from discord.ext import commands
from discord.ext.menus.views import ViewMenuPages

from helpers import checks, constants
from helpers.pagination import EmbedListPageSource

INVITE_REGEX = r"(?:https?://)?discord(?:app)?\.(?:com/invite|gg)/([a-zA-Z0-9]+)/?"
URL_REGEX = r"(?:https?:)?(?:\/\/)?(?:[^@\n]+@)?(?:www\.)?([^:\/\n]+)"


class AutomodModule(abc.ABC):
    bucket: str
    punishments: Dict[int, Tuple[str, Optional[timedelta]]]

    @abc.abstractmethod
    async def check(self, ctx):
        pass


class BannedWords(AutomodModule):
    bucket = "banned_words"
    punishments = {
        4: ("ban", None),
        3: ("timeout", timedelta(days=4)),
        2: ("timeout", timedelta(days=1)),
        1: ("timeout", timedelta(hours=2)),
        0: ("warn", None),
    }

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
        if isinstance(ctx.channel, discord.Thread) and ctx.channel.parent.id == 984579960037576855:
            return

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
    punishments = {
        2: ("ban", None),
        1: ("timeout", timedelta(days=3)),
        0: ("timeout", timedelta(hours=2)),
    }

    async def check(self, ctx):
        if ctx.channel.id == 722244899767844866:
            return
        if len(ctx.message.mentions) >= 10:
            return f"Sending too many mentions."


class ServerInvites(AutomodModule):
    bucket = "server_invites"
    punishments = {
        3: ("ban", None),
        2: ("timeout", timedelta(days=3)),
        1: ("timeout", timedelta(hours=2)),
        0: ("warn", None),
    }

    def __init__(self, bot):
        self.regex = re.compile(INVITE_REGEX, flags=re.I)
        self.bot = bot

    async def check(self, ctx):
        for code in self.regex.findall(ctx.message.content):
            with suppress(discord.NotFound):
                invite = await self.bot.fetch_invite(code)
                if invite.guild != ctx.guild:
                    return f"Sending invites to another server."


CATCHING_CATEGORY_ID = 717872471411261510


class Spamming(AutomodModule):
    bucket = "spamming"
    punishments = {
        5: ("ban", None),
        4: ("timeout", timedelta(days=3)),
        3: ("timeout", timedelta(days=1)),
        2: ("timeout", timedelta(hours=12)),
        1: ("timeout", timedelta(hours=6)),
        0: ("timeout", timedelta(hours=1)),
    }
    message_count = 10
    message_rate = 12.0  # seconds

    def __init__(self):
        self.cooldown = commands.CooldownMapping.from_cooldown(
            self.message_count, self.message_rate, commands.BucketType.member
        )
        self.catching_cooldown = commands.CooldownMapping.from_cooldown(
            self.message_count,
            self.message_rate,
            lambda msg: (commands.BucketType.channel(msg), commands.BucketType.member(msg)),
        )

    async def check(self, ctx):
        cooldown = (
            self.catching_cooldown
            if (ctx.channel.category and ctx.channel.category.id == CATCHING_CATEGORY_ID)
            else self.cooldown
        )

        bucket = cooldown.get_bucket(ctx.message)
        if bucket.update_rate_limit():
            cooldown._cache[cooldown._bucket_key(ctx.message)].reset()
            await ctx.channel.purge(limit=self.message_count, check=lambda m: m.author == ctx.author)
            return "Spamming"


class Automod(commands.Cog):
    """For moderation."""

    def __init__(self, bot):
        self.bot = bot
        self.banned_words = BannedWords(bot)
        self.modules = [self.banned_words, ServerInvites(bot), MassMention(), Spamming()]

    @commands.Cog.listener(name="on_message")
    @commands.Cog.listener(name="on_message_edit")
    async def on_message(self, *args):
        message = args[-1]

        if (
            message.guild is None
            or message.guild.id == constants.SUPPORT_SERVER_ID
            or not isinstance(message.author, discord.Member)
            or message.author.bot
            or message.channel.permissions_for(message.author).manage_messages
        ):
            return

        ctx = await self.bot.get_context(message)
        for module in self.modules:
            if reason := await module.check(ctx):
                await self.automod_punish(ctx, module, reason=reason)

    async def automod_punish(self, ctx, module, *, reason):
        with suppress(discord.Forbidden, discord.HTTPException):
            await ctx.message.delete()

        cog = self.bot.get_cog("Moderation")
        if cog is None:
            return

        query = {
            "target_id": ctx.author.id,
            "user_id": self.bot.user.id,
            "guild_id": ctx.guild.id,
            "created_at": {"$gt": datetime.now(timezone.utc) - timedelta(weeks=1)},
            "automod_bucket": module.bucket,
        }
        count = await self.bot.mongo.db.action.count_documents(query)

        kwargs = dict(
            target=ctx.author,
            user=self.bot.user,
            reason=f"Automod: {reason}",
            guild_id=ctx.guild.id,
            created_at=datetime.now(timezone.utc),
            automod_bucket=module.bucket,
        )

        type, duration = next(x for c, x in module.punishments.items() if count >= c)
        if duration is not None:
            kwargs["expires_at"] = kwargs["created_at"] + duration

        action = cog.cls_dict[type](**kwargs)
        await action.notify()
        await action.execute(ctx)

    @commands.hybrid_group()
    @checks.is_server_admin()
    async def automod(self, ctx):
        """Utilities for automoderation."""

        await ctx.send_help(ctx.command)

    @automod.group(fallback="list")
    @checks.is_server_admin()
    async def words(self, ctx):
        """Displays the banned words list.

        You must have the Community Manager role to use this.
        """

        pages = ViewMenuPages(
            source=EmbedListPageSource(
                await self.banned_words.fetch(ctx.guild),
                title="Banned Words",
                show_index=True,
            )
        )
        await pages.start(ctx)

    @words.command()
    @checks.is_server_admin()
    async def add(self, ctx, words):
        """Adds words to the banned words list.

        You must have the Community Manager role to use this.
        """

        words = words.split()
        await self.banned_words.update(ctx.guild, push=[x.casefold() for x in words])
        words_msg = ", ".join(f"**{x}**" for x in words)
        await ctx.send(f"Added {words_msg} to the banned words list.")

    @words.command()
    @checks.is_server_admin()
    async def remove(self, ctx, words):
        """Removes words from the banned words list.

        You must have the Community Manager role to use this.
        """

        words = words.split()
        await self.banned_words.update(ctx.guild, pull=[x.casefold() for x in words])
        words_msg = ", ".join(f"**{x}**" for x in words)
        await ctx.send(f"Removed {words_msg} from the banned words list.")


async def setup(bot):
    await bot.add_cog(Automod(bot))
