import abc
from collections import Counter
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Union

import discord
from discord.channel import CategoryChannel
from discord.ext import commands, tasks
from discord.ext.events.utils import fetch_recent_audit_log_entry
from discord.ext.menus.views import ViewMenuPages
from helpers import checks, time
from helpers.pagination import AsyncEmbedFieldsPageSource
from helpers.utils import FakeUser, FetchUserConverter


class ModerationUserFriendlyTime(time.UserFriendlyTime):
    def __init__(self):
        super().__init__(commands.clean_content, default="No reason provided")


def message_channel(ctx, message):
    if isinstance(message, discord.TextChannel):
        return dict(message_id=message.last_message_id, channel_id=message.id)
    message = message or ctx.message
    return dict(message_id=message.id, channel_id=message.channel.id)


@dataclass
class Action(abc.ABC):
    target: discord.Member
    user: discord.Member
    reason: str
    guild_id: int
    channel_id: int = None
    message_id: int = None
    created_at: datetime = None
    expires_at: datetime = None
    note: str = None
    automod_bucket: str = None
    resolved: bool = None
    _id: int = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now(timezone.utc)
        if self.expires_at is not None:
            self.resolved = False

    @classmethod
    def build_from_mongo(cls, bot, x):
        guild = bot.get_guild(x["guild_id"])
        user = guild.get_member(x["user_id"]) or FakeUser(x["user_id"])
        target = guild.get_member(x["target_id"]) or FakeUser(x["target_id"])
        kwargs = {
            "_id": x["_id"],
            "target": target,
            "user": user,
            "reason": x["reason"],
            "guild_id": x["guild_id"],
            "channel_id": x.get("channel_id"),
            "message_id": x.get("message_id"),
            "created_at": x["created_at"],
        }
        if "expires_at" in x:
            kwargs["expires_at"] = x["expires_at"]
            kwargs["resolved"] = x["resolved"]
        if "automod_bucket" in x:
            kwargs["automod_bucket"] = x["automod_bucket"]
        if "note" in x:
            kwargs["note"] = x["note"]
        return cls_dict[x["type"]](**kwargs)

    @property
    def duration(self):
        if self.expires_at is None:
            return None
        return self.expires_at - self.created_at

    @property
    def logs_url(self):
        if self.message_id is None or self.channel_id is None:
            return None
        return f"https://admin.poketwo.net/logs/{self.guild_id}/{self.channel_id}?before={self.message_id+1}"

    def to_dict(self):
        base = {
            "target_id": self.target.id,
            "user_id": self.user.id,
            "type": self.type,
            "reason": self.reason,
            "guild_id": self.guild_id,
            "channel_id": self.channel_id,
            "message_id": self.message_id,
            "created_at": self.created_at,
        }
        if self.expires_at is not None:
            base["resolved"] = self.resolved
            base["expires_at"] = self.expires_at
        if self.automod_bucket is not None:
            base["automod_bucket"] = self.automod_bucket
        return base

    def to_user_embed(self):
        embed = discord.Embed(
            title=f"{self.emoji} {self.past_tense.title()}",
            description=f"You have been {self.past_tense}.",
            color=self.color,
        )
        reason = self.reason or "No reason provided"
        embed.add_field(name="Reason", value=reason, inline=False)
        if self.duration is not None:
            embed.add_field(name="Duration", value=time.human_timedelta(self.duration))
            embed.set_footer(text="Expires")
            embed.timestamp = self.expires_at
        return embed

    def to_log_embed(self):
        reason = self.reason or "No reason provided"
        if self.logs_url is not None:
            reason += f" ([Logs]({self.logs_url}))"

        embed = discord.Embed(color=self.color)
        embed.set_author(
            name=f"{self.user} (ID: {self.user.id})", icon_url=self.user.display_avatar.url
        )
        embed.set_thumbnail(url=self.target.display_avatar.url)
        embed.add_field(
            name=f"{self.emoji} {self.past_tense.title()} {self.target} (ID: {self.target.id})",
            value=reason,
        )
        if self.duration is not None:
            embed.set_footer(text=f"Duration • {time.human_timedelta(self.duration)}\nExpires")
            embed.timestamp = self.expires_at
        return embed

    def to_info_embed(self):
        reason = self.reason or "No reason provided"
        embed = discord.Embed(
            color=self.color, title=f"{self.emoji} {self.past_tense.title()} {self.target}"
        )
        embed.set_author(name=f"{self.user}", icon_url=self.user.display_avatar.url)
        embed.set_thumbnail(url=self.target.display_avatar.url)
        embed.add_field(name="Reason", value=reason, inline=False)
        if self.note is not None:
            embed.add_field(name="Note", value=self.note)
        if self.logs_url is not None:
            embed.add_field(name="Logs", value=f"[Link]({self.logs_url})", inline=False)
        if self.duration is not None:
            duration = f"{time.human_timedelta(self.duration)}"
            expires_at = f"{discord.utils.format_dt(self.expires_at)} ({discord.utils.format_dt(self.expires_at, 'R')}"
            embed.add_field(name="Duration", value=duration, inline=False)
            embed.add_field(name="Expires At", value=expires_at, inline=False)
        embed.timestamp = self.created_at
        return embed

    async def notify(self):
        with suppress(discord.Forbidden, discord.HTTPException):
            await self.target.send(embed=self.to_user_embed())

    @abc.abstractmethod
    async def execute(self, ctx):
        await ctx.bot.get_cog("Moderation").save_action(self)


class Kick(Action):
    type = "kick"
    past_tense = "kicked"
    emoji = "\N{WOMANS BOOTS}"
    color = discord.Color.orange()

    async def execute(self, ctx):
        reason = self.reason or f"Action done by {self.user} (ID: {self.user.id})"
        await ctx.guild.kick(self.target, reason=reason)
        await super().execute(ctx)


class Ban(Action):
    type = "ban"
    past_tense = "banned"
    emoji = "\N{HAMMER}"
    color = discord.Color.red()

    def to_user_embed(self):
        embed = super().to_user_embed()
        embed.description += " Please do not DM staff members to get unpunished. If you would like to appeal, [click here](https://forms.poketwo.net/)."
        return embed

    async def execute(self, ctx):
        reason = self.reason or f"Action done by {self.user} (ID: {self.user.id})"
        await ctx.guild.ban(self.target, reason=reason)
        await super().execute(ctx)


class Unban(Action):
    type = "unban"
    past_tense = "unbanned"
    emoji = "\N{OPEN LOCK}"
    color = discord.Color.green()

    async def execute(self, ctx):
        reason = self.reason or f"Action done by {self.user} (ID: {self.user.id})"
        await ctx.guild.unban(self.target, reason=reason)
        await super().execute(ctx)


class Warn(Action):
    type = "warn"
    past_tense = "warned"
    emoji = "\N{WARNING SIGN}"
    color = discord.Color.orange()

    async def execute(self, ctx):
        await super().execute(ctx)


class Mute(Action):
    type = "mute"
    past_tense = "muted"
    emoji = "\N{SPEAKER WITH CANCELLATION STROKE}"
    color = discord.Color.blue()

    async def execute(self, ctx):
        reason = self.reason or f"Action done by {self.user} (ID: {self.user.id})"
        role = discord.utils.get(ctx.guild.roles, name="Muted")
        await self.target.add_roles(role, reason=reason)
        await ctx.bot.mongo.db.member.update_one(
            {"_id": self.target.id}, {"$set": {"muted": True}}, upsert=True
        )
        await super().execute(ctx)


class Unmute(Action):
    type = "unmute"
    past_tense = "unmuted"
    emoji = "\N{SPEAKER}"
    color = discord.Color.green()

    async def execute(self, ctx):
        reason = self.reason or f"Action done by {self.user} (ID: {self.user.id})"
        role = discord.utils.get(ctx.guild.roles, name="Muted")
        await self.target.remove_roles(role, reason=reason)
        await ctx.bot.mongo.db.member.update_one(
            {"_id": self.target.id}, {"$set": {"muted": False}}, upsert=True
        )
        await super().execute(ctx)


class TradingMute(Action):
    type = "trading_mute"
    past_tense = "muted in trading"
    emoji = "\N{SPEAKER WITH CANCELLATION STROKE}"
    color = discord.Color.blue()

    async def execute(self, ctx):
        reason = self.reason or f"Action done by {self.user} (ID: {self.user.id})"
        role = discord.utils.get(ctx.guild.roles, name="Trading Muted")
        role2 = discord.utils.get(ctx.guild.roles, name="Trading")
        await self.target.add_roles(role, reason=reason)
        await self.target.remove_roles(role2, reason=reason)
        await ctx.bot.mongo.db.member.update_one(
            {"_id": self.target.id}, {"$set": {"trading_muted": True}}, upsert=True
        )
        await super().execute(ctx)


class TradingUnmute(Action):
    type = "trading_unmute"
    past_tense = "unmuted in trading"
    emoji = "\N{SPEAKER}"
    color = discord.Color.green()

    async def execute(self, ctx):
        reason = self.reason or f"Action done by {self.user} (ID: {self.user.id})"
        role = discord.utils.get(ctx.guild.roles, name="Trading Muted")
        await self.target.remove_roles(role, reason=reason)
        await ctx.bot.mongo.db.member.update_one(
            {"_id": self.target.id}, {"$set": {"trading_muted": False}}, upsert=True
        )
        await super().execute(ctx)


@dataclass
class FakeContext:
    bot: commands.Bot
    guild: discord.Guild


cls_dict = {x.type: x for x in (Kick, Ban, Unban, Warn, Mute, Unmute, TradingMute, TradingUnmute)}


class BanConverter(commands.Converter):
    async def convert(self, ctx, arg):
        try:
            return await ctx.guild.fetch_ban(discord.Object(id=int(arg)))
        except discord.NotFound:
            raise commands.BadArgument("This member is not banned.")
        except ValueError:
            pass

        bans = await ctx.guild.bans()
        ban = discord.utils.find(lambda u: str(u.user) == arg, bans)
        if ban is None:
            raise commands.BadArgument("This member is not banned.")
        return ban


class MemberOrIdConverter(commands.Converter):
    async def convert(self, ctx, arg):
        with suppress(commands.MemberNotFound):
            return await commands.MemberConverter().convert(ctx, arg)

        try:
            return FakeUser(int(arg))
        except ValueError:
            raise commands.MemberNotFound(arg)


class Moderation(commands.Cog):
    """For moderation."""

    def __init__(self, bot):
        self.bot = bot
        self.cls_dict = cls_dict
        self.check_actions.start()

    @commands.Cog.listener()
    async def on_member_join(self, member):
        data = await self.bot.mongo.db.member.find_one(
            {"_id": member.id, "guild_id": member.guild.id}
        )
        if data is None:
            return
        ctx = FakeContext(self.bot, member.guild)
        kwargs = dict(
            target=member,
            user=self.bot.user,
            reason="User rejoined guild",
            guild_id=member.guild.id,
        )
        if data.get("muted", False):
            await Mute(**kwargs).execute(ctx)
        if data.get("trading_muted", False):
            await TradingMute(**kwargs).execute(ctx)

    async def save_action(self, action: Action):
        await self.bot.mongo.db.action.update_many(
            {
                "target_id": action.target.id,
                "guild_id": action.guild_id,
                "type": action.type,
                "resolved": False,
            },
            {"$set": {"resolved": True}},
        )
        action._id = await self.bot.mongo.reserve_id("action")
        await self.bot.mongo.db.action.insert_one({"_id": action._id, **action.to_dict()})

        data = await self.bot.mongo.db.guild.find_one({"_id": action.guild_id})
        channel = self.bot.get_channel(data["logs_channel_id"])
        if channel is not None:
            await channel.send(embed=action.to_log_embed())

    @commands.Cog.listener()
    async def on_member_ban(self, guild, target):
        """Logs ban events not made through the bot."""

        entry = await fetch_recent_audit_log_entry(
            self.bot, guild, target=target, action=discord.AuditLogAction.ban, retry=3
        )
        if entry.user == self.bot.user:
            return

        action = Ban(
            target=target,
            user=entry.user,
            reason=entry.reason,
            guild_id=guild.id,
            created_at=entry.created_at,
        )
        await self.save_action(action)

    @commands.Cog.listener()
    async def on_member_unban(self, guild, target):
        entry = await fetch_recent_audit_log_entry(
            self.bot, guild, target=target, action=discord.AuditLogAction.unban, retry=3
        )
        if entry.user == self.bot.user:
            return

        action = Unban(
            target=target,
            user=entry.user,
            reason=entry.reason,
            guild_id=guild.id,
            created_at=entry.created_at,
        )
        await self.save_action(action)

    @commands.Cog.listener()
    async def on_member_kick(self, target, entry):
        if entry.user == self.bot.user:
            return

        action = Kick(
            target=target,
            user=entry.user,
            reason=entry.reason,
            guild_id=target.guild.id,
            created_at=entry.created_at,
        )
        await self.save_action(action)

    async def run_purge(self, ctx, limit, check):
        await ctx.message.delete()
        deleted = await ctx.channel.purge(limit=limit, check=check, before=ctx.message)
        spammers = Counter(m.author.display_name for m in deleted)
        count = len(deleted)

        messages = [f'{count} message{" was" if count == 1 else "s were"} removed.']
        if len(deleted) > 0:
            messages.append("")
            spammers = sorted(spammers.items(), key=lambda t: t[1], reverse=True)
            messages.extend(f"– **{author}**: {count}" for author, count in spammers)

        await ctx.send("\n".join(messages), delete_after=5)

    @commands.command()
    @commands.guild_only()
    @checks.is_moderator()
    async def cleanup(self, ctx, search=100):
        """Cleans up the bot's messages from the channel.

        You must have the Moderator Role to use this.
        """

        await self.run_purge(
            ctx, search, lambda m: m.author == ctx.me or m.content.startswith(ctx.prefix)
        )

    @commands.group(invoke_without_command=True, aliases=("remove", "clean", "clear"))
    @commands.guild_only()
    @checks.is_moderator()
    async def purge(self, ctx, search: Union[discord.Member, int]):
        """Mass deletes messages that meet a certain criteria.

        If no subcommand is called, purges either all messages from a user or
        all messages, depending on the argument provided.

        You must have the Moderator role to use this.
        """

        if isinstance(search, discord.Member):
            await ctx.invoke(self.user, user=search)
        else:
            await ctx.invoke(self.all, search=search)

    @purge.command()
    @checks.is_moderator()
    async def all(self, ctx, search: int = 100):
        """Purges all messages."""
        await self.run_purge(ctx, search, lambda m: True)

    @purge.command()
    @checks.is_moderator()
    async def user(self, ctx, user: discord.Member, search: int = 100):
        """Purges messages from a user."""
        await self.run_purge(ctx, search, lambda m: m.author == user)

    @purge.command()
    @checks.is_moderator()
    async def contains(self, ctx, *text):
        """Purges messages that contain a substring."""
        search = 100
        if text[-1].isdigit():
            text, search = text[:-1], int(text[-1])
        await self.run_purge(
            ctx, search, lambda m: " ".join(text).casefold() in m.content.casefold()
        )

    @commands.command()
    @commands.guild_only()
    @checks.is_moderator()
    async def warn(self, ctx, target: discord.Member, *, reason):
        """Warns a member in the server.

        You must have the Moderator role to use this.
        """

        if any(role.id in checks.MODERATOR_ROLES for role in getattr(target, "roles", [])):
            return await ctx.send("You can't punish that person!")

        action = Warn(
            target=target,
            user=ctx.author,
            reason=reason,
            guild_id=ctx.guild.id,
            created_at=ctx.message.created_at,
        )
        await action.execute(ctx)
        await action.notify()
        await ctx.send(f"Warned **{target}** (Case #{action._id}).")

    @commands.command()
    @commands.guild_only()
    @checks.is_moderator()
    async def kick(self, ctx, target: discord.Member, *, reason):
        """Kicks a member from the server.

        You must have the Moderator role to use this.
        """

        if any(role.id in checks.MODERATOR_ROLES for role in getattr(target, "roles", [])):
            return await ctx.send("You can't punish that person!")

        action = Kick(
            target=target,
            user=ctx.author,
            reason=reason,
            guild_id=ctx.guild.id,
            created_at=ctx.message.created_at,
        )
        await action.notify()
        await action.execute(ctx)
        await ctx.send(f"Kicked **{target}** (Case #{action._id}).")

    @commands.command(usage="<target> [expires_at] [reason]")
    @commands.guild_only()
    @checks.is_moderator()
    async def ban(
        self, ctx, target: MemberOrIdConverter, *, reason: Union[ModerationUserFriendlyTime, str]
    ):
        """Bans a member from the server.

        You must have the Moderator role to use this.
        """

        if any(role.id in checks.MODERATOR_ROLES for role in getattr(target, "roles", [])):
            return await ctx.send("You can't punish that person!")

        if isinstance(reason, time.UserFriendlyTime):
            expires_at = reason.dt
            reason = reason.arg
        else:
            expires_at = None

        action = Ban(
            target=target,
            user=ctx.author,
            reason=reason,
            guild_id=ctx.guild.id,
            created_at=ctx.message.created_at,
            expires_at=expires_at,
        )
        await action.notify()
        await action.execute(ctx)
        if action.duration is None:
            await ctx.send(f"Banned **{target}** (Case #{action._id}).")
        else:
            await ctx.send(
                f"Banned **{target}** for **{time.human_timedelta(action.duration)}** (Case #{action._id})."
            )

    @commands.command()
    @commands.guild_only()
    @checks.is_moderator()
    async def unban(self, ctx, target: BanConverter, *, reason=None):
        """Unbans a member from the server.

        You must have the Moderator role to use this.
        """

        action = Unban(
            target=target.user,
            user=ctx.author,
            reason=reason,
            guild_id=ctx.guild.id,
        )
        await action.execute(ctx)
        await ctx.send(f"Unbanned **{target.user}** (Case #{action._id}).")

    @commands.group(invoke_without_command=True, usage="<target> [expires_at] [reason]")
    @commands.guild_only()
    @checks.is_moderator()
    async def mute(
        self, ctx, target: discord.Member, *, reason: Union[ModerationUserFriendlyTime, str]
    ):
        """Mutes a member in the server.

        You must have the Moderator role to use this.
        """

        if any(role.id in checks.MODERATOR_ROLES for role in getattr(target, "roles", [])):
            return await ctx.send("You can't punish that person!")

        if isinstance(reason, time.UserFriendlyTime):
            expires_at = reason.dt
            reason = reason.arg
        else:
            expires_at = None

        action = Mute(
            target=target,
            user=ctx.author,
            reason=reason,
            guild_id=ctx.guild.id,
            created_at=ctx.message.created_at,
            expires_at=expires_at,
        )
        await action.execute(ctx)
        await action.notify()
        if action.duration is None:
            await ctx.send(f"Muted **{target}** (Case #{action._id}).")
        else:
            await ctx.send(
                f"Muted **{target}** for **{time.human_timedelta(action.duration)}** (Case #{action._id})."
            )

    @commands.command()
    @commands.guild_only()
    @checks.is_moderator()
    async def unmute(self, ctx, target: discord.Member, *, reason=None):
        """Unmutes a member in the server.

        You must have the Moderator role to use this.
        """

        action = Unmute(
            target=target,
            user=ctx.author,
            reason=reason,
            guild_id=ctx.guild.id,
        )
        await action.execute(ctx)
        await action.notify()
        await ctx.send(f"Unmuted **{target}** (Case #{action._id}).")

    @mute.command(aliases=("sync",))
    @checks.is_community_manager()
    async def setup(self, ctx):
        """Sets up the Muted role's permissions.

        You must have the Community Manager role to use this.
        """

        role = discord.utils.get(ctx.guild.roles, name="Muted")
        if role is None:
            return await ctx.send("Please create a role named Muted first.")

        for channel in ctx.guild.channels:
            if isinstance(channel, CategoryChannel) or not channel.permissions_synced:
                await channel.set_permissions(
                    role, send_messages=False, add_reactions=False, speak=False, stream=False
                )

        await ctx.send("Set up permissions for the Muted role.")

    @commands.command(aliases=("tmute",), usage="<target> [expires_at] [reason]")
    @commands.guild_only()
    @checks.is_moderator()
    async def tradingmute(
        self, ctx, target: discord.Member, *, reason: Union[ModerationUserFriendlyTime, str]
    ):
        """Mutes a member in trading channels.

        You must have the Moderator role to use this.
        """

        if any(role.id in checks.MODERATOR_ROLES for role in getattr(target, "roles", [])):
            return await ctx.send("You can't punish that person!")

        if isinstance(reason, time.UserFriendlyTime):
            expires_at = reason.dt
            reason = reason.arg
        else:
            expires_at = None

        action = TradingMute(
            target=target,
            user=ctx.author,
            reason=reason,
            guild_id=ctx.guild.id,
            created_at=ctx.message.created_at,
            expires_at=expires_at,
        )
        await action.execute(ctx)
        await action.notify()
        if action.duration is None:
            await ctx.send(f"Muted **{target}** in trading channels (Case #{action._id}).")
        else:
            await ctx.send(
                f"Muted **{target}** in trading channels for **{time.human_timedelta(action.duration)}** (Case #{action._id})."
            )

    @commands.command(aliases=("untradingmute", "tunmute", "untmute"))
    @commands.guild_only()
    @checks.is_moderator()
    async def tradingunmute(self, ctx, target: discord.Member, *, reason=None):
        """Unmutes a member in trading channels.

        You must have the Moderator role to use this.
        """

        action = TradingUnmute(
            target=target,
            user=ctx.author,
            reason=reason,
            guild_id=ctx.guild.id,
        )
        await action.execute(ctx)
        await action.notify()
        await ctx.send(f"Unmuted **{target}** in trading channels (Case #{action._id}).")

    async def reverse_raw_action(self, raw_action):
        action = Action.build_from_mongo(self.bot, raw_action)

        guild = self.bot.get_guild(action.guild_id)
        target = action.target

        if action.type == "ban":
            action_type = Unban
            try:
                ban = await guild.fetch_ban(discord.Object(id=raw_action["target_id"]))
            except (ValueError, discord.NotFound):
                return
            target = ban.user
        elif action.type == "mute":
            action_type = Unmute
        elif action.type == "trading_mute":
            action_type = TradingUnmute
        else:
            return

        new_action = action_type(
            target=target,
            user=self.bot.user,
            reason="Punishment duration expired",
            guild_id=action.guild_id,
            created_at=datetime.now(timezone.utc),
        )

        await new_action.execute(FakeContext(self.bot, guild))
        await new_action.notify()

        await self.bot.mongo.db.action.update_one(
            {"_id": raw_action["_id"]}, {"$set": {"resolved": True}}
        )

    @tasks.loop(seconds=30)
    async def check_actions(self):
        await self.bot.wait_until_ready()
        query = {"resolved": False, "expires_at": {"$lt": datetime.now(timezone.utc)}}

        async for action in self.bot.mongo.db.action.find(query):
            self.bot.loop.create_task(self.reverse_raw_action(action))

    @commands.group(invoke_without_command=True)
    @commands.guild_only()
    @checks.is_moderator()
    async def history(self, ctx, *, target: Union[discord.Member, FetchUserConverter]):
        """Views a member's punishment history.

        You must have the Moderator role to use this.
        """

        query = {"target_id": target.id, "guild_id": ctx.guild.id}
        count = await self.bot.mongo.db.action.count_documents(query)

        async def get_actions():
            async for x in self.bot.mongo.db.action.find(query).sort("created_at", -1):
                yield Action.build_from_mongo(self.bot, x)

        def format_item(i, x):
            name = f"{x._id}. {x.emoji} {x.past_tense.title()} by {x.user}"
            reason = x.reason or "No reason provided"
            lines = [
                f"– **Reason:** {reason}",
                f"– at {discord.utils.format_dt(x.created_at)} ({discord.utils.format_dt(x.created_at, 'R')})",
            ]
            if x.duration is not None:
                lines.insert(1, f"– **Duration:** {time.human_timedelta(x.duration)}")
            if x.note is not None:
                lines.insert(1, f"– **Note:** {x.note}")
            return {"name": name, "value": "\n".join(lines), "inline": False}

        pages = ViewMenuPages(
            source=AsyncEmbedFieldsPageSource(
                get_actions(),
                title=f"Punishment History • {target}",
                format_item=format_item,
                count=count,
            )
        )

        try:
            await pages.start(ctx)
        except IndexError:
            await ctx.send("No punishment history found.")

    @history.command(aliases=("del",))
    @commands.guild_only()
    @checks.is_moderator()
    async def delete(self, ctx, ids: commands.Greedy[int]):
        """Deletes one or more entries from punishment history.

        You must have the Moderator role to use this.
        """

        result = await self.bot.mongo.db.action.delete_many(
            {"_id": {"$in": ids}, "guild_id": ctx.guild.id}
        )
        word = "entry" if result.deleted_count == 1 else "entries"
        await ctx.send(f"Successfully deleted {result.deleted_count} {word}.")

    @history.command()
    @commands.guild_only()
    @checks.is_moderator()
    async def note(self, ctx, id: int, *, note):
        """Adds a note to an entry from punishment history.

        You must have the Moderator role to use this.
        """

        result = await self.bot.mongo.db.action.find_one_and_update(
            {"_id": id, "guild_id": ctx.guild.id}, {"$set": {"note": note}}
        )
        if result is None:
            return await ctx.send("Could not find an entry with that ID.")
        if note.lower() == "reset":
            await self.bot.mongo.db.action.update_one(
                {"_id": id, "guild_id": ctx.guild.id}, {"$unset": {"note": 1}}
            )
            return await ctx.send(f"Successfully removed note of entry **{id}**.")

        await ctx.send(f"Successfully added a note to entry **{id}**.")

    @history.command(aliases=("show",))
    @commands.guild_only()
    @checks.is_moderator()
    async def info(self, ctx, id: int):
        """Shows an entry from punishment history.

        You must have the Moderator role to use this.
        """

        action = await self.bot.mongo.db.action.find_one({"_id": id, "guild_id": ctx.guild.id})
        if action is None:
            return await ctx.send("Could not find an entry with that ID.")

        action = Action.build_from_mongo(self.bot, action)
        await ctx.send(embed=action.to_info_embed())

    @commands.command(cooldown_after_parsing=True)
    @commands.cooldown(1, 20, commands.BucketType.user)
    async def report(self, ctx, user: discord.Member, *, reason):
        """Reports a user to server moderators."""

        data = await self.bot.mongo.db.guild.find_one({"_id": ctx.guild.id})
        channel = ctx.guild.get_channel_or_thread(data["report_channel_id"])

        await channel.send(
            f"{ctx.author.mention} reported {user.mention} in {ctx.channel.mention} for:\n> {reason}"
        )
        await ctx.send(f"Reported **{user}**.")

    def cog_unload(self):
        self.check_actions.cancel()


def setup(bot):
    bot.add_cog(Moderation(bot))
