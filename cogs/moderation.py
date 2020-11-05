from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum

import config
import discord
from bson.objectid import ObjectId
from discord.ext import commands
from discord.ext.events.utils import fetch_recent_audit_log_entry

LOG_CHANNEL = 720552022754983999
STAFF_ROLE = 721825360827777043


class ActionType(IntEnum):
    KICK = 1
    BAN = 2
    UNBAN = 3
    WARN = 4
    MUTE = 5
    UNMUTE = 6

    @property
    def past_tense(self):
        if self == self.KICK:
            return "kicked"
        elif self == self.BAN:
            return "banned"
        elif self == self.UNBAN:
            return "unbanned"
        elif self == self.WARN:
            return "warned"
        elif self == self.MUTE:
            return "muted"
        elif self == self.UNMUTE:
            return "unmuted"

    @property
    def emoji(self):
        if self == self.KICK:
            return "\N{WOMANS BOOTS}"
        elif self == self.BAN:
            return "\N{HAMMER}"
        elif self == self.UNBAN:
            return "\N{OPEN LOCK}"
        elif self == self.WARN:
            return "\N{WARNING SIGN}"
        elif self == self.MUTE:
            return "\N{SPEAKER WITH CANCELLATION STROKE}"
        elif self == self.UNMUTE:
            return "\N{SPEAKER}"

    @property
    def color(self):
        if self == self.KICK:
            return discord.Color.orange()
        elif self == self.BAN:
            return discord.Color.red()
        elif self == self.UNBAN:
            return discord.Color.green()
        elif self == self.WARN:
            return discord.Color.orange()
        elif self == self.MUTE:
            return discord.Color.blue()
        elif self == self.UNMUTE:
            return discord.Color.green()


@dataclass
class Action:
    target_id: int
    user_id: int
    type: ActionType
    reason: str
    created_at: datetime = None
    _id: ObjectId = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.utcnow()

    @property
    def id(self):
        return self._id

    def to_dict(self):
        return dict(
            target_id=self.target_id,
            user_id=self.user_id,
            type=self.type,
            reason=self.reason,
            created_at=self.created_at,
        )


@dataclass
class TempAction(Action):
    resolved: bool = False
    expires_at: datetime = None

    @property
    def duration(self):
        return self.expires_at - self.created_at

    def to_dict(self):
        return dict(
            **super().to_dict(),
            resolved=self.resolved,
            expires_at=self.expires_at,
        )


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

    def to_user_embed(self, action):
        embed = discord.Embed(
            title=f"{action.type.emoji} {action.type.past_tense.title()}",
            description=f"You have been {action.type.past_tense}.",
            color=action.type.color,
        )
        embed.add_field(
            name="Reason",
            value=action.reason or "No reason provided",
            inline=False,
        )
        if isinstance(action, TempAction):
            embed.add_field(name="Duration", value=action.duration)
        embed.timestamp = action.created_at
        return embed

    async def send_log_message(self, *args, **kwargs):
        channel = self.bot.get_channel(LOG_CHANNEL)
        await channel.send(*args, **kwargs)

    async def send_action_message(self, target, action):
        try:
            await target.send(embed=self.to_user_embed(action))
        except (discord.Forbidden, discord.HTTPException):
            pass

    @commands.Cog.listener()
    async def on_action_perform(self, target, user, action):
        embed = discord.Embed(color=action.type.color)
        embed.set_author(name=f"{user} (ID: {user.id})", icon_url=user.avatar_url)
        embed.set_thumbnail(url=target.avatar_url)
        embed.add_field(
            name=f"{action.type.emoji} {action.type.past_tense.title()} {target} (ID: {target.id})",
            value=action.reason or "No reason provided",
        )
        if isinstance(action, TempAction):
            embed.set_footer(text=f"Duration: {action.duration}")
        embed.timestamp = action.created_at

        await self.bot.db.action.insert_one(action.to_dict())
        await self.send_log_message(embed=embed)

    @commands.Cog.listener()
    async def on_member_ban(self, guild, target):
        entry = await fetch_recent_audit_log_entry(
            self.bot, guild, target=target, action=discord.AuditLogAction.ban, retry=3
        )
        if entry.user == self.bot.user:
            return

        action = Action(
            target_id=target.id,
            user_id=entry.user.id,
            type=ActionType.BAN,
            reason=entry.reason,
        )
        self.bot.dispatch("action_perform", target, entry.user, action)

    @commands.Cog.listener()
    async def on_member_unban(self, guild, target):
        entry = await fetch_recent_audit_log_entry(
            self.bot, guild, target=target, action=discord.AuditLogAction.unban, retry=3
        )
        if entry.user == self.bot.user:
            return

        action = Action(
            target_id=target.id,
            user_id=entry.user.id,
            type=ActionType.UNBAN,
            reason=entry.reason,
        )
        self.bot.dispatch("action_perform", target, entry.user, action)

    @commands.Cog.listener()
    async def on_member_kick(self, target, entry):
        if entry.user == self.bot.user:
            return

        action = Action(
            target_id=target.id,
            user_id=entry.user.id,
            type=ActionType.KICK,
            reason=entry.reason,
        )
        self.bot.dispatch("action_perform", target, entry.user, action)

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
    async def kick(self, ctx, target: discord.Member, *, reason=None):
        """Kicks a member from the server.

        You must have Kick Members permission to use this.
        """

        if any(x.id == STAFF_ROLE for x in target.roles):
            return await ctx.send("You can't punish staff members!")

        action = Action(
            target_id=target.id,
            user_id=ctx.author.id,
            type=ActionType.KICK,
            reason=reason,
        )

        await self.send_action_message(target, action)
        await ctx.guild.kick(
            target,
            reason=reason or f"Action done by {ctx.author} (ID: {ctx.author.id})",
        )
        await ctx.send(f"Kicked **{target}**.")

        self.bot.dispatch("action_perform", target, ctx.author, action)

    @commands.command()
    @commands.guild_only()
    @commands.has_permissions(ban_members=True)
    async def ban(self, ctx, target: discord.Member, *, reason=None):
        """Bans a member from the server.

        You must have Ban Members permission to use this.
        """

        if any(x.id == STAFF_ROLE for x in target.roles):
            return await ctx.send("You can't punish staff members!")

        action = Action(
            target_id=target.id,
            user_id=ctx.author.id,
            type=ActionType.BAN,
            reason=reason,
        )

        await self.send_action_message(target, action)
        await ctx.guild.ban(
            target,
            reason=reason or f"Action done by {ctx.author} (ID: {ctx.author.id})",
        )
        await ctx.send(f"Banned **{target}**.")

        self.bot.dispatch("action_perform", target, ctx.author, action)

    @commands.command()
    @commands.guild_only()
    @commands.has_permissions(ban_members=True)
    async def unban(self, ctx, target: BanConverter, *, reason=None):
        """Unbans a member from the server.

        You must have Ban Members permission to use this.
        """

        action = Action(
            target_id=target.user.id,
            user_id=ctx.author.id,
            type=ActionType.UNBAN,
            reason=reason,
        )

        await self.send_action_message(target.user, action)
        await ctx.guild.unban(
            target.user,
            reason=reason or f"Action done by {ctx.author} (ID: {ctx.author.id})",
        )
        await ctx.send(f"Unbanned **{target.user}**.")

        self.bot.dispatch("action_perform", target.user, ctx.author, action)


def setup(bot):
    bot.add_cog(Moderation(bot))
