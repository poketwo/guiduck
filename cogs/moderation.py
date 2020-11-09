import abc
import typing
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

import config
import discord
from discord.ext import commands, menus, tasks
from discord.ext.events.utils import fetch_recent_audit_log_entry
from helpers import time
from helpers.pagination import AsyncFieldsPageSource
from helpers.utils import FakeUser

LOG_CHANNEL = 720552022754983999
STAFF_ROLE = 721825360827777043
GUILD_ID = 716390832034414685

TimeDelta = typing.Optional[time.TimeDelta]


@dataclass
class Action(abc.ABC):
    target: discord.Member
    user: discord.Member
    reason: str
    created_at: datetime = None
    expires_at: datetime = None
    resolved: bool = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.utcnow()
        if self.expires_at is not None:
            self.resolved = False

    @classmethod
    def build_from_mongo(cls, bot, x):
        user = bot.get_user(x["user_id"]) or FakeUser(x["user_id"])
        target = bot.get_user(x["target_id"]) or FakeUser(x["target_id"])
        kwargs = {
            "target": target,
            "user": user,
            "reason": x["reason"],
            "created_at": x["created_at"],
        }
        if "expires_at" in x:
            kwargs["expires_at"] = x["expires_at"]
            kwargs["resolved"] = x["resolved"]
        return cls_dict[x["type"]](**kwargs)

    @property
    def duration(self):
        if self.expires_at is None:
            return None
        return self.expires_at - self.created_at

    def to_dict(self):
        base = {
            "target_id": self.target.id,
            "user_id": self.user.id,
            "type": self.type,
            "reason": self.reason,
            "created_at": self.created_at,
        }
        if self.expires_at is not None:
            base["resolved"] = self.resolved
            base["expires_at"] = self.expires_at
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
            embed.add_field(
                name="Duration", value=time.strfdelta(self.duration, long=True)
            )
            embed.set_footer(text="Expires")
            embed.timestamp = self.expires_at
        return embed

    def to_log_embed(self):
        embed = discord.Embed(color=self.color)
        embed.set_author(
            name=f"{self.user} (ID: {self.user.id})", icon_url=self.user.avatar_url
        )
        embed.set_thumbnail(url=self.target.avatar_url)
        embed.add_field(
            name=f"{self.emoji} {self.past_tense.title()} {self.target} (ID: {self.target.id})",
            value=self.reason or "No reason provided",
        )
        if self.duration is not None:
            embed.set_footer(
                text=f"Duration • {time.strfdelta(self.duration, long=True)}\nExpires"
            )
            embed.timestamp = self.expires_at
        return embed

    async def notify(self):
        try:
            await self.target.send(embed=self.to_user_embed())
        except (discord.Forbidden, discord.HTTPException):
            pass

    @abc.abstractmethod
    async def execute(self, ctx):
        pass


class Kick(Action):
    type = "kick"
    past_tense = "kicked"
    emoji = "\N{WOMANS BOOTS}"
    color = discord.Color.orange()

    async def execute(self, ctx):
        reason = self.reason or f"Action done by {self.user} (ID: {self.user.id})"
        await ctx.guild.kick(self.target, reason=reason)


class Ban(Action):
    type = "ban"
    past_tense = "banned"
    emoji = "\N{HAMMER}"
    color = discord.Color.red()

    async def execute(self, ctx):
        reason = self.reason or f"Action done by {self.user} (ID: {self.user.id})"
        await ctx.guild.ban(self.target, reason=reason)


class Unban(Action):
    type = "unban"
    past_tense = "unbanned"
    emoji = "\N{OPEN LOCK}"
    color = discord.Color.green()

    async def execute(self, ctx):
        reason = self.reason or f"Action done by {self.user} (ID: {self.user.id})"
        await ctx.guild.unban(self.target, reason=reason)


class Warn(Action):
    type = "warn"
    past_tense = "warned"
    emoji = "\N{WARNING SIGN}"
    color = discord.Color.orange()

    async def execute(self, ctx):
        pass


class Mute(Action):
    type = "mute"
    past_tense = "muted"
    emoji = "\N{SPEAKER WITH CANCELLATION STROKE}"
    color = discord.Color.blue()

    async def execute(self, ctx):
        reason = self.reason or f"Action done by {self.user} (ID: {self.user.id})"
        role = discord.utils.get(ctx.guild.roles, name="Muted")
        await self.target.add_roles(role, reason=reason)


class Unmute(Action):
    type = "unmute"
    past_tense = "unmuted"
    emoji = "\N{SPEAKER}"
    color = discord.Color.green()

    async def execute(self, ctx):
        reason = self.reason or f"Action done by {self.user} (ID: {self.user.id})"
        role = discord.utils.get(ctx.guild.roles, name="Muted")
        await self.target.remove_roles(role, reason=reason)


class TradingMute(Action):
    type = "trading_mute"
    past_tense = "muted in trading"
    emoji = "\N{SPEAKER WITH CANCELLATION STROKE}"
    color = discord.Color.blue()

    async def execute(self, ctx):
        reason = self.reason or f"Action done by {self.user} (ID: {self.user.id})"
        role = discord.utils.get(ctx.guild.roles, name="Trading Muted")
        await self.target.add_roles(role, reason=reason)


class TradingUnmute(Action):
    type = "trading_unmute"
    past_tense = "unmuted in trading"
    emoji = "\N{SPEAKER}"
    color = discord.Color.green()

    async def execute(self, ctx):
        reason = self.reason or f"Action done by {self.user} (ID: {self.user.id})"
        role = discord.utils.get(ctx.guild.roles, name="Trading Muted")
        await self.target.remove_roles(role, reason=reason)


cls_dict = {
    x.type: x
    for x in (Kick, Ban, Unban, Warn, Mute, Unmute, TradingMute, TradingUnmute)
}


@dataclass
class FakeContext:
    guild: discord.Guild


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


class Moderation(commands.Cog):
    """For moderation."""

    def __init__(self, bot):
        self.bot = bot
        self.check_actions.start()

    async def send_log_message(self, *args, **kwargs):
        channel = self.bot.get_channel(LOG_CHANNEL)
        await channel.send(*args, **kwargs)

    @commands.Cog.listener()
    async def on_action_perform(self, action):
        await self.bot.db.action.update_many(
            {"target_id": action.target.id, "type": action.type, "resolved": False},
            {"$set": {"resolved": True}},
        )
        await self.bot.db.action.insert_one(action.to_dict())
        await self.send_log_message(embed=action.to_log_embed())

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
            created_at=entry.created_at,
        )
        self.bot.dispatch("action_perform", action)

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
            created_at=entry.created_at,
        )
        self.bot.dispatch("action_perform", action)

    @commands.Cog.listener()
    async def on_member_kick(self, target, entry):
        if entry.user == self.bot.user:
            return

        action = Kick(
            target=target,
            user=entry.user,
            reason=entry.reason,
            created_at=entry.created_at,
        )
        self.bot.dispatch("action_perform", action)

    @commands.command()
    @commands.has_permissions(manage_messages=True)
    async def cleanup(self, ctx, search=100):
        """Cleans up the bot's messages from the channel.

        You must have Manage Messages permission to use this.
        """

        def check(m):
            return m.author == ctx.me or m.content.startswith(config.PREFIX)

        deleted = await ctx.channel.purge(limit=search, check=check, before=ctx.message)
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
    @commands.has_permissions(kick_members=True)
    async def warn(self, ctx, target: discord.Member, *, reason=None):
        """Warns a member in the server.

        You must have Kick Members permission to use this.
        """

        if any(x.id == STAFF_ROLE for x in target.roles):
            return await ctx.send("You can't punish staff members!")

        action = Warn(target=target, user=ctx.author, reason=reason)
        await action.execute(ctx)
        await action.notify()
        await ctx.send(f"Warned **{target}**.")
        self.bot.dispatch("action_perform", action)

    @commands.command()
    @commands.guild_only()
    @commands.has_permissions(kick_members=True)
    async def kick(self, ctx, target: discord.Member, *, reason=None):
        """Kicks a member from the server.

        You must have Kick Members permission to use this.
        """

        if any(x.id == STAFF_ROLE for x in target.roles):
            return await ctx.send("You can't punish staff members!")

        action = Kick(target=target, user=ctx.author, reason=reason)
        await action.notify()
        await action.execute(ctx)
        await ctx.send(f"Kicked **{target}**.")
        self.bot.dispatch("action_perform", action)

    @commands.command()
    @commands.guild_only()
    @commands.has_permissions(ban_members=True)
    async def ban(
        self, ctx, target: discord.Member, duration: TimeDelta = None, *, reason=None
    ):
        """Bans a member from the server.

        You must have Ban Members permission to use this.
        """

        if any(x.id == STAFF_ROLE for x in target.roles):
            return await ctx.send("You can't punish staff members!")

        created_at = datetime.utcnow()
        expires_at = None
        if duration is not None:
            expires_at = created_at + duration

        action = Ban(
            target=target,
            user=ctx.author,
            reason=reason,
            created_at=created_at,
            expires_at=expires_at,
        )
        await action.notify()
        await action.execute(ctx)
        if action.duration is None:
            await ctx.send(f"Banned **{target}**.")
        else:
            await ctx.send(f"Banned **{target}** for **{time.strfdelta(duration)}**.")
        self.bot.dispatch("action_perform", action)

    @commands.command()
    @commands.guild_only()
    @commands.has_permissions(ban_members=True)
    async def unban(self, ctx, target: BanConverter, *, reason=None):
        """Unbans a member from the server.

        You must have Ban Members permission to use this.
        """

        action = Unban(target=target.user, user=ctx.author, reason=reason)
        await action.execute(ctx)
        await ctx.send(f"Unbanned **{target.user}**.")
        self.bot.dispatch("action_perform", action)

    @commands.command()
    @commands.guild_only()
    @commands.has_permissions(kick_members=True)
    async def mute(
        self, ctx, target: discord.Member, duration: TimeDelta = None, *, reason=None
    ):
        """Mutes a member in the server.

        You must have Kick Members permission to use this.
        """

        print(duration)

        if any(x.id == STAFF_ROLE for x in target.roles):
            return await ctx.send("You can't punish staff members!")

        created_at = datetime.utcnow()
        expires_at = None
        if duration is not None:
            expires_at = created_at + duration

        action = Mute(
            target=target,
            user=ctx.author,
            reason=reason,
            created_at=created_at,
            expires_at=expires_at,
        )
        await action.execute(ctx)
        await action.notify()
        if action.duration is None:
            await ctx.send(f"Muted **{target}**.")
        else:
            await ctx.send(f"Muted **{target}** for **{time.strfdelta(duration)}**.")
        self.bot.dispatch("action_perform", action)

    @commands.command()
    @commands.guild_only()
    @commands.has_permissions(kick_members=True)
    async def unmute(self, ctx, target: discord.Member, *, reason=None):
        """Unmutes a member in the server.

        You must have Kick Members permission to use this.
        """

        action = Unmute(target=target, user=ctx.author, reason=reason)
        await action.execute(ctx)
        await action.notify()
        await ctx.send(f"Unmuted **{target}**.")
        self.bot.dispatch("action_perform", action)

    @commands.command(aliases=("tmute",))
    @commands.guild_only()
    @commands.has_permissions(kick_members=True)
    async def tradingmute(
        self, ctx, target: discord.Member, duration: TimeDelta = None, *, reason=None
    ):
        """Mutes a member in trading channels.

        You must have Kick Members permission to use this.
        """

        print(duration)

        if any(x.id == STAFF_ROLE for x in target.roles):
            return await ctx.send("You can't punish staff members!")

        created_at = datetime.utcnow()
        expires_at = None
        if duration is not None:
            expires_at = created_at + duration

        action = TradingMute(
            target=target,
            user=ctx.author,
            reason=reason,
            created_at=created_at,
            expires_at=expires_at,
        )
        await action.execute(ctx)
        await action.notify()
        if action.duration is None:
            await ctx.send(f"Muted **{target}** in trading channels.")
        else:
            await ctx.send(
                f"Muted **{target}** in trading channels for **{time.strfdelta(duration)}**."
            )
        self.bot.dispatch("action_perform", action)

    @commands.command(aliases=("tunmute",))
    @commands.guild_only()
    @commands.has_permissions(kick_members=True)
    async def tradingunmute(self, ctx, target: discord.Member, *, reason=None):
        """Unmutes a member in trading channels.

        You must have Kick Members permission to use this.
        """

        action = TradingUnmute(target=target, user=ctx.author, reason=reason)
        await action.execute(ctx)
        await action.notify()
        await ctx.send(f"Unmuted **{target}** in trading channels.")
        self.bot.dispatch("action_perform", action)

    async def reverse_raw_action(self, raw_action):
        guild = self.bot.get_guild(GUILD_ID)

        if raw_action["type"] == "ban":
            action_type = Unban
            try:
                ban = await guild.fetch_ban(discord.Object(id=raw_action["target_id"]))
            except (ValueError, discord.NotFound):
                return
            target = ban.user
        elif raw_action["type"] == "mute":
            action_type = Unmute
            target = guild.get_member(raw_action["target_id"])
        elif raw_action["type"] == "trading_mute":
            action_type = TradingUnmute
            target = guild.get_member(raw_action["target_id"])
        else:
            return

        action = action_type(
            target=target,
            user=self.bot.user,
            reason="Punishment duration expired",
            created_at=datetime.utcnow(),
        )

        await action.execute(FakeContext(guild))
        await action.notify()
        self.bot.dispatch("action_perform", action)

        await self.bot.db.action.update_one(
            {"_id": raw_action["_id"]}, {"$set": {"resolved": True}}
        )

    @tasks.loop(seconds=30)
    async def check_actions(self):
        await self.bot.wait_until_ready()
        query = {"resolved": False, "expires_at": {"$lt": datetime.utcnow()}}

        async for action in self.bot.db.action.find(query):
            self.bot.loop.create_task(self.reverse_raw_action(action))

    @commands.command(aliases=("warnings",))
    @commands.guild_only()
    @commands.has_permissions(kick_members=True)
    async def history(self, ctx, *, target: discord.Member):
        """Views a member's punishment history.

        You must have Kick Members permission to use this.
        """

        query = {"target_id": target.id}

        async def get_actions():
            async for x in self.bot.db.action.find(query).sort("_id", 1):
                yield Action.build_from_mongo(self.bot, x)

        def format_item(i, x):
            name = f"{i+1}. {x.emoji} {x.past_tense.title()} by {x.user}"
            reason = x.reason or "No reason provided"
            lines = [
                f"– **Reason:** {reason}",
                f"– at {x.created_at:%m-%d-%y %I:%M %p}",
            ]
            if x.duration is not None:
                lines.insert(1, f"– **Duration:** {time.strfdelta(x.duration)}")
            return {"name": name, "value": "\n".join(lines), "inline": False}

        pages = menus.MenuPages(
            source=AsyncFieldsPageSource(
                get_actions(),
                title=f"Punishment History • {target}",
                format_item=format_item,
            )
        )

        try:
            await pages.start(ctx)
        except IndexError:
            await ctx.send("No punishment history found.")

    def cog_unload(self):
        self.check_actions.cancel()


def setup(bot):
    bot.add_cog(Moderation(bot))
