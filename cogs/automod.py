import re
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

import discord
from discord.ext import commands

from helpers import constants


class AutomodModule:
    bucket: str
    punishments: Dict[int, Tuple[str, Optional[timedelta]]]


class BannedWords(AutomodModule):
    bucket = "banned_words"
    punishments = {
        4: ("ban", None),
        3: ("timeout", timedelta(days=4)),
        2: ("timeout", timedelta(days=1)),
        1: ("timeout", timedelta(hours=2)),
        0: ("warn", None),
    }


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
        self.modules = [MassMention(), Spamming()]

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


async def setup(bot):
    await bot.add_cog(Automod(bot))
