import re
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

import discord
from discord.ext import commands

from cogs.moderation import FakeContext
from helpers import constants


class AutomodModule:
    bucket: str
    punishments: Dict[int, Tuple[str, Optional[timedelta]]]


class BannedWords(AutomodModule):
    bucket = "banned_words"
    rule_name = "Guiduck: Banned Words"
    punishments = {
        4: ("ban", None),
        3: ("timeout", timedelta(days=4)),
        2: ("timeout", timedelta(days=1)),
        1: ("timeout", timedelta(hours=2)),
        0: ("warn", None),
    }

    def automod_reason(self, execution):
        return f"The word `{execution.matched_keyword.strip('*')}` is banned, watch your language."


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
    rule_name = "Guiduck: Server Invites"
    punishments = {
        3: ("ban", None),
        2: ("timeout", timedelta(days=3)),
        1: ("timeout", timedelta(hours=2)),
        0: ("warn", None),
    }

    def automod_reason(self, execution):
        return "Sending invites to another server."


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
        self.rule_modules = {m.rule_name: m for m in (BannedWords(), ServerInvites())}

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
                await self.automod_punish(message.guild, message.author, module, reason=reason, message=message)

    @commands.Cog.listener()
    async def on_automod_action(self, execution):
        """Discord AutoMod blocked a message matching one of our rules; punish as if we had caught it ourselves."""

        if execution.action.type != discord.AutoModRuleActionType.block_message or execution.guild is None:
            return

        rule = await execution.fetch_rule()
        if (module := self.rule_modules.get(rule.name)) is None:
            return

        member = execution.member or await execution.guild.fetch_member(execution.user_id)
        if member.bot or (execution.channel is not None and execution.channel.permissions_for(member).manage_messages):
            return

        await self.automod_punish(execution.guild, member, module, reason=module.automod_reason(execution))

    async def automod_punish(self, guild, member, module, *, reason, message=None):
        if message is not None:
            with suppress(discord.Forbidden, discord.HTTPException):
                await message.delete()

        cog = self.bot.get_cog("Moderation")
        if cog is None:
            return

        query = {
            "target_id": member.id,
            "user_id": self.bot.user.id,
            "guild_id": guild.id,
            "created_at": {"$gt": datetime.now(timezone.utc) - timedelta(weeks=1)},
            "automod_bucket": module.bucket,
        }
        count = await self.bot.mongo.db.action.count_documents(query)

        kwargs = dict(
            target=member,
            user=self.bot.user,
            reason=f"Automod: {reason}",
            guild_id=guild.id,
            created_at=datetime.now(timezone.utc),
            automod_bucket=module.bucket,
        )

        type, duration = next(x for c, x in module.punishments.items() if count >= c)
        if duration is not None:
            kwargs["expires_at"] = kwargs["created_at"] + duration

        action = cog.cls_dict[type](**kwargs)
        await action.notify()
        await action.execute(FakeContext(self.bot, guild))


async def setup(bot):
    await bot.add_cog(Automod(bot))
