import re
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

import discord
from discord.ext import commands
from discord.ext.menus.views import ViewMenuPages

from cogs.moderation import FakeContext
from helpers import checks, constants
from helpers.pagination import EmbedListPageSource


def _split_tolerant(text):
    return r"\s*".join(re.escape(c) for c in text)


# Allows whitespace/newlines between characters, so "https://disc\nord\n.gg/x" is caught too.
INVITE_REGEX = (
    rf"(?i){_split_tolerant('discord')}(?:{_split_tolerant('app')})?\s*\.\s*"
    rf"(?:{_split_tolerant('com/invite')}|{_split_tolerant('gg')})\s*/\s*[a-z0-9]+"
)

BANNED_WORDS_EXEMPT_CHANNEL_ID = 984579960037576855


class AutomodModule:
    bucket: str
    punishments: Dict[int, Tuple[str, Optional[timedelta]]]


class BannedWords(AutomodModule):
    bucket = "banned_words"
    rule_name = "Banned Words [managed by Guiduck, do not rename]"
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
    rule_name = "Server Invites [managed by Guiduck, do not rename]"
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

        if (
            execution.action.type != discord.AutoModRuleActionType.block_message
            or execution.guild is None
            or execution.guild.id == constants.SUPPORT_SERVER_ID
        ):
            return

        rule = await execution.fetch_rule()
        if (module := self.rule_modules.get(rule.name)) is None:
            return

        member = execution.member or await execution.guild.fetch_member(execution.user_id)
        if member.bot or (execution.channel is not None and execution.channel.permissions_for(member).manage_messages):
            return

        await self.automod_punish(execution.guild, member, module, reason=module.automod_reason(execution))

    async def get_rule(self, guild, name):
        return discord.utils.get(await guild.fetch_automod_rules(), name=name)

    async def set_rule(self, ctx, name, trigger, exempt_channels=()):
        kwargs = dict(
            trigger=trigger,
            actions=[discord.AutoModRuleAction(type=discord.AutoModRuleActionType.block_message)],
            exempt_roles=[role for role in ctx.guild.roles if role.permissions.manage_messages],
            exempt_channels=list(exempt_channels),
            enabled=True,
            reason=f"Action done by {ctx.author} (ID: {ctx.author.id})",
        )
        if (rule := await self.get_rule(ctx.guild, name)) is None:
            await ctx.guild.create_automod_rule(
                name=name, event_type=discord.AutoModRuleEventType.message_send, **kwargs
            )
        else:
            await rule.edit(**kwargs)

    async def banned_words(self, guild):
        rule = await self.get_rule(guild, BannedWords.rule_name)
        return [] if rule is None else [w.strip("*") for w in rule.trigger.keyword_filter]

    async def set_banned_words(self, ctx, words):
        if not words:
            if (rule := await self.get_rule(ctx.guild, BannedWords.rule_name)) is not None:
                await rule.delete()
            return
        # Bare keywords match whole words; domains have to match inside URLs.
        trigger = discord.AutoModTrigger(keyword_filter=[f"*{w}*" if "." in w else w for w in words])
        await self.set_rule(
            ctx, BannedWords.rule_name, trigger, exempt_channels=[discord.Object(BANNED_WORDS_EXEMPT_CHANNEL_ID)]
        )

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

    @commands.hybrid_group()
    @checks.is_server_admin()
    async def automod(self, ctx):
        """Utilities for automoderation."""

        await ctx.send_help(ctx.command)

    @automod.command()
    @checks.is_server_admin()
    async def invites(self, ctx):
        """Creates or updates the Discord AutoMod rule that blocks invites to other servers.

        Invites to this server are allow-listed by code, so re-run this after creating new permanent invites.
        """

        codes = [invite.code for invite in await ctx.guild.invites()]
        if ctx.guild.vanity_url_code is not None:
            codes.insert(0, ctx.guild.vanity_url_code)
        # Allow-list entries are exact, so split links to this server are blocked too, as are foreign codes that
        # merely start with one of ours. Discord caps the allow list at 100 entries, i.e. 50 codes (vanity first).
        allow_list = [f"*{host}/{code}" for code in codes for host in ("discord.gg", "/invite")]
        trigger = discord.AutoModTrigger(regex_patterns=[INVITE_REGEX], allow_list=allow_list[:100])
        await self.set_rule(ctx, ServerInvites.rule_name, trigger)
        await ctx.send("Updated the server invites AutoMod rule.")

    @automod.group(fallback="list")
    @checks.is_server_admin()
    async def words(self, ctx):
        """Displays the banned words list.

        You must have the Community Manager role to use this.
        """

        pages = ViewMenuPages(
            source=EmbedListPageSource(await self.banned_words(ctx.guild), title="Banned Words", show_index=True)
        )
        await pages.start(ctx)

    @words.command()
    @checks.is_server_admin()
    async def add(self, ctx, words):
        """Adds words to the banned words list.

        You must have the Community Manager role to use this.
        """

        words = words.casefold().split()
        current = await self.banned_words(ctx.guild)
        await self.set_banned_words(ctx, list(dict.fromkeys(current + words)))
        words_msg = ", ".join(f"**{x}**" for x in words)
        await ctx.send(f"Added {words_msg} to the banned words list.")

    @words.command()
    @checks.is_server_admin()
    async def remove(self, ctx, words):
        """Removes words from the banned words list.

        You must have the Community Manager role to use this.
        """

        words = words.casefold().split()
        current = await self.banned_words(ctx.guild)
        await self.set_banned_words(ctx, [x for x in current if x not in words])
        words_msg = ", ".join(f"**{x}**" for x in words)
        await ctx.send(f"Removed {words_msg} from the banned words list.")


async def setup(bot):
    await bot.add_cog(Automod(bot))
