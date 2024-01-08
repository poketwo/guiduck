import abc
from collections import Counter
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import textwrap
from typing import Optional, Union

import discord
from discord.ext import commands, tasks
from discord.ext.events.utils import fetch_recent_audit_log_entry
from discord.ext.menus.views import ViewMenuPages
from discord.ui import button

from helpers import checks, constants, time
from helpers.context import GuiduckContext
from helpers.pagination import AsyncEmbedFieldsPageSource
from helpers.utils import FakeUser, FetchUserConverter, with_attachment_urls


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
        embed.set_author(name=f"{self.user} (ID: {self.user.id})", icon_url=self.user.display_avatar.url)
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
        embed = discord.Embed(color=self.color, title=f"{self.emoji} {self.past_tense.title()} {self.target}")
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


class Note(Action):
    type = "note"
    past_tense = "noted"
    emoji = "\N{MEMO}"
    color = discord.Color.yellow()

    async def execute(self, ctx):
        await super().execute(ctx)


class Timeout(Action):
    type = "timeout"
    past_tense = "placed in timeout"
    emoji = "\N{SPEAKER WITH CANCELLATION STROKE}"
    color = discord.Color.blue()

    async def execute(self, ctx):
        reason = self.reason or f"Action done by {self.user} (ID: {self.user.id})"
        await self.target.edit(timed_out_until=self.expires_at, reason=reason)
        await super().execute(ctx)


class _Untimeout(Action):
    type = "untimeout"
    past_tense = "removed from timeout"
    emoji = "\N{SPEAKER}"
    color = discord.Color.green()


class Untimeout(_Untimeout):
    async def execute(self, ctx):
        reason = self.reason or f"Action done by {self.user} (ID: {self.user.id})"
        await self.target.edit(timed_out_until=None, reason=reason)
        await super().execute(ctx)


class SymbolicUntimeout(_Untimeout):
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
            {"_id": {"id": self.target.id, "guild_id": ctx.guild.id}},
            {"$set": {"muted": True}},
            upsert=True,
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
            {"_id": {"id": self.target.id, "guild_id": ctx.guild.id}},
            {"$set": {"muted": False}},
            upsert=True,
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
            {"_id": {"id": self.target.id, "guild_id": ctx.guild.id}},
            {"$set": {"trading_muted": True}},
            upsert=True,
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
            {"_id": {"id": self.target.id, "guild_id": ctx.guild.id}},
            {"$set": {"trading_muted": False}},
            upsert=True,
        )
        await super().execute(ctx)


class EmergencyAlertBan(Action):
    type = "emergency_alert_ban"
    past_tense = "banned from emergency staff alerts"
    emoji = "\N{BELL WITH CANCELLATION STROKE}"
    color = discord.Color.magenta()

    async def execute(self, ctx):
        await ctx.bot.mongo.db.member.update_one(
            {"_id": {"id": self.target.id, "guild_id": ctx.guild.id}},
            {"$set": {"emergency_alert_banned_until": self.expires_at or True}},
            upsert=True,
        )
        await super().execute(ctx)


class EmergencyAlertUnban(Action):
    type = "emergency_alert_unban"
    past_tense = "removed from emergency staff alert ban"
    emoji = "\N{BELL}"
    color = discord.Color.green()

    async def execute(self, ctx):
        await ctx.bot.mongo.db.member.update_one(
            {"_id": {"id": self.target.id, "guild_id": ctx.guild.id}},
            {"$unset": {"emergency_alert_banned_until": 1}},
            upsert=True,
        )
        await super().execute(ctx)


@dataclass
class FakeContext:
    bot: commands.Bot
    guild: discord.Guild


cls_dict = {
    x.type: x for x in (Kick, Ban, Unban, Warn, Note, Timeout, Untimeout, Mute, Unmute, TradingMute, TradingUnmute, EmergencyAlertBan, EmergencyAlertUnban)
}


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


EMERGENCY_COOLDOWN_HOURS = 1

class EmergencyView(discord.ui.View):
    def __init__(self, ctx: GuiduckContext):
        super().__init__(timeout=None)
        self.ctx = ctx
        self.message: discord.Message

    @discord.ui.button(label="Resolve", style=discord.ButtonStyle.green)
    async def resolve(self, interaction: discord.Interaction, button: discord.Button):
        await interaction.response.defer()
        button.label = "Resolved"
        button.disabled = True

        embed = self.message.embeds[0]
        embed.color = discord.Color.green()
        embed.set_footer(text=f"Resolved by @{interaction.user} ({interaction.user.id})")
        await self.message.edit(embed=embed, view=self)

    async def interaction_check(self, interaction: discord.Interaction):
        user = interaction.user
        checks = (
            any(role.id in constants.TRIAL_MODERATOR_ROLES for role in getattr(user, "roles", []))
            or user.id in {self.ctx.bot.owner_id, self.ctx.author.id, *self.ctx.bot.owner_ids}
        )
        if not checks:
            await interaction.response.send_message("You can't use this!", ephemeral=True)
            return False
        return True

class Moderation(commands.Cog):
    """For moderation."""

    def __init__(self, bot):
        self.bot = bot
        self.cls_dict = cls_dict
        self.check_actions.start()

    @commands.Cog.listener()
    async def on_member_join(self, member):
        data = await self.bot.mongo.db.member.find_one({"_id": {"id": member.id, "guild_id": member.guild.id}})
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
    async def on_member_update(self, before, after):
        if after.timed_out_until == before.timed_out_until:
            return

        if after.timed_out_until is not None and after.timed_out_until < datetime.now(timezone.utc):
            return

        entry = await fetch_recent_audit_log_entry(
            self.bot,
            after.guild,
            target=after,
            action=discord.AuditLogAction.member_update,
            retry=3,
        )
        if entry.user == self.bot.user:
            return

        if after.timed_out_until is None:
            action_cls = SymbolicUntimeout
        else:
            action_cls = Timeout

        action = action_cls(
            target=after,
            user=entry.user,
            reason=entry.reason,
            guild_id=after.guild.id,
            created_at=entry.created_at,
            expires_at=after.timed_out_until,
        )
        await action.notify()
        await self.save_action(action)

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

    @commands.hybrid_group(aliases=("emergency-staff", "alert", "alert-staff"), cooldown_after_parsing=True, fallback="send", invoke_without_subcommand=True)
    @commands.cooldown(1, EMERGENCY_COOLDOWN_HOURS*60*60, commands.BucketType.guild)  # Cooldown per guild
    @commands.guild_only()
    async def emergency(self, ctx: GuiduckContext, *, reason: str):
        """Emergency command to alert staff members.

        Do no abuse. Meant for use during emergencies that need immediate staff attention.
        """

        member = await self.bot.mongo.db.member.find_one({"_id": {"id": ctx.author.id, "guild_id": ctx.guild.id}})
        if until := member.get("emergency_alert_banned_until"):
            if until is True:
                return await ctx.send("You've been permanently banned from issuing emergency staff alerts due to violation(s) of its rules. If you think that this was a mistake, please contact a staff member.")
            elif until is not None and until > (now := datetime.now(timezone.utc)):
                seconds = (until - now).total_seconds()
                return await ctx.send(f"You've been banned from issuing emergency staff alerts for **{time.human_timedelta(timedelta(seconds=seconds))}** due to violation(s) of its rules.")

        guild = await self.bot.mongo.db.guild.find_one({"_id": ctx.guild.id})
        role = ctx.guild.get_role(guild.get("emergency_alert_role")) if guild else None
        if role is None:
            return await ctx.send("Emergency Staff Alert role not found for this guild. Please ask an Administrator to set one up.", ephemeral=True)

        number_staff = len(role.members)
        confirm_embed = discord.Embed(
            color=discord.Color.red(),
            title="🚨 Emergency Staff Alert",
            description=textwrap.dedent(
                f"""
                This command is designed for use in case of emergencies that need immediate staff attention. This will ping **{number_staff}** staff member{'' if number_staff == 1 else 's'} currently assigned to the {role.mention} role, and you will be assisted shortly.
                """
            )
        )
        confirm_embed.add_field(
            name="Are you sure that you want to send an Emergency Staff Alert for the following reason?",
            value=reason,
            inline=False
        )

        rules_embed = discord.Embed(
            color=discord.Color.blurple(),
            title="Use Cases & Abuse",
            description=textwrap.dedent(
                f"""
                Abuse of this alert system is **strictly prohibited** and **will** result in repercussions if used maliciously. Here are some examples to help understand when and when not to use it, this is not exhaustive:
                **✅ Acceptable Cases**
                - Actively sending NSFW/disturbing content in our server(s)/DMs
                - Advertising Crosstrading/Distribution of automated scripts in our server(s) that violate Pokétwo TOS
                - Malicious/excessive spam in our server(s)
                - Advertising links to malicious/scam websites in our server(s)/DMs
                - Violating any other rule to an excessive extent
                **<:white_cross_mark:1193650425166045224> Unacceptable Cases**
                - Suspected autocatching in our server(s)
                - Server advertisement
                - Toxicity/Harrassment
                - Bot outages/bugs/glitches — Please use #bug-reports or ping a Developer in case of emergency
                - To ask staff to check appeals/applications
                """
            )
        )
        rules_embed.set_footer(text="Please use `?report` in unacceptable cases that violate our rules.")

        if not await ctx.confirm(timeout=120, embeds=[confirm_embed, rules_embed], ephemeral=True):
            ctx.command.reset_cooldown(ctx)
            return await ctx.send("Aborted.", ephemeral=True)

        alert_embed = discord.Embed(
            color=discord.Color.red(),
            title="🚨 Emergency Staff Alert Issued",
            description=""
        )
        alert_embed.set_author(
            name=f"{ctx.author} ({ctx.author.id})",
            icon_url=ctx.author.display_avatar,
        )
        alert_embed.add_field(
            name="Reason",
            value=reason,
            inline=False
        )
        view = EmergencyView(ctx)
        view.add_item(discord.ui.Button(label="Logs", url=f"https://admin.poketwo.net/logs/{ctx.guild.id}/{ctx.channel.id}?before={ctx.message.id+1}"))
        view.message = await ctx.reply(role.mention, embed=alert_embed, view=view)

    @emergency.error
    async def emergency_error(self, ctx: GuiduckContext, error):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(
                f"An Emergency Staff Alert has already been issued recently and is currently on cooldown. Please use `?report` instead if necessary.",
                ephemeral=True,
            )

    @emergency.command(name="set-role")
    @checks.is_community_manager()
    @commands.guild_only()
    async def emergency_set_role(self, ctx: GuiduckContext, *, role: Optional[discord.Role] = None):
        """Set the role for emergency alerts for a guild.

        You must have the Community Manager role to use this.
        """

        await self.bot.mongo.db.guild.update_one({"_id": ctx.guild.id}, {"$set": {"emergency_alert_role": getattr(role, "id", role)}}, upsert=True)
        await ctx.send(f"{f'Set {role.mention} as' if role else 'Unset'} the Emergency Staff Alert role for this guild.")

    @emergency.command(name="ban", usage="<target> [expires_at] [reason]")
    @checks.is_trial_moderator()
    @commands.guild_only()
    async def emergency_ban(self, ctx: GuiduckContext, target: discord.Member, *, time_and_reason):
        """Temporarily or permanently bans a member from using the Emergency Staff Alert command.

        You must have the Trial Moderator role to use this.
        """

        if any(role.id in constants.TRIAL_MODERATOR_ROLES for role in getattr(target, "roles", [])):
            return await ctx.send("You can't punish that person!", ephemeral=True)

        expires_at, reason = await self.parse_time_and_reason(ctx, time_and_reason)

        action = EmergencyAlertBan(
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
            await ctx.send(f"Permanently banned **{target}** from issuing emergency staff alerts (Case #{action._id}).", ephemeral=True)
        else:
            await ctx.send(
                f"Banned **{target}** from issuing emergency staff alerts for **{time.human_timedelta(action.duration)}** (Case #{action._id}).",
                ephemeral=True,
            )

    @emergency.command(name="unban", usage="<target> [reason]")
    @checks.is_trial_moderator()
    @commands.guild_only()
    async def emergency_unban(self, ctx: GuiduckContext, target: discord.Member, *, reason=None):
        """Unbans a member who has been banned from using the Emergency Staff Alert command.

        You must have the Trial Moderator role to use this.
        """

        action = EmergencyAlertUnban(
            target=target,
            user=ctx.author,
            reason=reason,
            guild_id=ctx.guild.id,
        )
        await action.execute(ctx)
        await action.notify()

        await ctx.send(
            f"Unbanned **{target}** from issuing emergency staff alerts (Case #{action._id}).",
            ephemeral=True,
        )

    async def run_purge(self, ctx, limit, check):
        class ConfirmPurgeView(discord.ui.View):
            @button(label=f"Purge up to {limit} messages", style=discord.ButtonStyle.danger)
            async def confirm(_self, interaction: discord.Interaction, button: discord.ui.Button):
                if interaction.user != ctx.author:
                    return
                _self.stop()
                await interaction.message.delete()
                await self._purge(ctx, limit, check)

            @button(label="Cancel")
            async def cancel(_self, interaction: discord.Interaction, button: discord.ui.Button):
                if interaction.user != ctx.author:
                    return
                _self.stop()
                await interaction.message.edit("The operation has been canceled.", view=None)

        if limit > 10000:
            await ctx.send("Too many messages to purge.", ephemeral=True)
        elif limit > 100:
            view = ConfirmPurgeView()
            await ctx.send(f"Are you sure you want to purge up to {limit} messages?", view=view, ephemeral=True)
        else:
            await self._purge(ctx, limit, check)

    async def _purge(self, ctx, limit, check):
        await ctx.message.delete()
        deleted = await ctx.channel.purge(limit=limit, check=check, before=ctx.message)
        spammers = Counter(m.author.display_name for m in deleted)
        count = len(deleted)

        messages = [f'{count} message{" was" if count == 1 else "s were"} removed.']
        if len(deleted) > 0:
            messages.append("")
            spammers = sorted(spammers.items(), key=lambda t: t[1], reverse=True)
            messages.extend(f"– **{author}**: {count}" for author, count in spammers)

        await ctx.send("\n".join(messages), delete_after=5, ephemeral=True)

    @commands.hybrid_command()
    @commands.guild_only()
    @checks.is_trial_moderator()
    async def cleanup(self, ctx, search=100):
        """Cleans up the bot's messages from the channel.

        You must have the Trial Moderator role to use this.
        """

        await self.run_purge(ctx, search, lambda m: m.author == ctx.me or m.content.startswith(ctx.prefix))

    @commands.hybrid_group(aliases=("remove", "clean", "clear"))
    @commands.guild_only()
    @checks.is_trial_moderator()
    async def purge(self, ctx, search: Union[discord.Member, int]):
        """Mass deletes messages that meet a certain criteria.

        If no subcommand is called, purges either all messages from a user or
        all messages, depending on the argument provided.

        You must have the Trial Moderator role to use this.
        """

        if isinstance(search, discord.Member):
            await ctx.invoke(self.user, user=search)
        else:
            await ctx.invoke(self.all, search=search)

    @purge.command()
    @checks.is_trial_moderator()
    async def all(self, ctx, search: int = 100):
        """Purges all messages."""
        await self.run_purge(ctx, search, lambda m: True)

    @purge.command()
    @checks.is_trial_moderator()
    async def user(self, ctx, user: discord.Member, search: int = 100):
        """Purges messages from a user."""
        await self.run_purge(ctx, search, lambda m: m.author == user)

    @purge.command()
    @checks.is_trial_moderator()
    async def contains(self, ctx, *, text):
        """Purges messages that contain a substring."""
        text = text.split()
        search = 100
        if text[-1].isdigit() and len(text) > 1:
            text, search = text[:-1], int(text[-1])
        await self.run_purge(ctx, search, lambda m: " ".join(text).casefold() in m.content.casefold())

    async def parse_time_and_reason(self, ctx, time_and_reason):
        try:
            reason = await ModerationUserFriendlyTime().convert(ctx, time_and_reason)
        except commands.BadArgument:
            return None, time_and_reason
        else:
            return reason.dt, reason.arg

    @commands.hybrid_command()
    @commands.guild_only()
    @checks.is_trial_moderator()
    async def warn(self, ctx, target: discord.Member, *, reason):
        """Warns a member in the server.

        You must have the Trial Moderator role to use this.
        """

        if any(role.id in constants.TRIAL_MODERATOR_ROLES for role in getattr(target, "roles", [])):
            return await ctx.send("You can't punish that person!", ephemeral=True)

        action = Warn(
            target=target,
            user=ctx.author,
            reason=reason,
            guild_id=ctx.guild.id,
            created_at=ctx.message.created_at,
        )
        await action.execute(ctx)
        await action.notify()
        await ctx.send(f"Warned **{target}** (Case #{action._id}).", ephemeral=True)

    @commands.hybrid_command()
    @commands.guild_only()
    @checks.is_trial_moderator()
    async def note(self, ctx, target: Union[discord.Member, discord.User], *, note: Optional[str] = ""):
        """Silently add a note to a user's history without the need of a parent history entry.

        You must have the Trial Moderator role to use this.
        """

        note = with_attachment_urls(note, ctx.message.attachments)

        if len(note) == 0:
            return await ctx.send_help(ctx.command)
        elif len(note) > constants.EMBED_FIELD_CHAR_LIMIT:
            return await ctx.send(f"History notes (including attachment URLs) can be at most {constants.EMBED_FIELD_CHAR_LIMIT} characters.")

        action = Note(
            target=target,
            user=ctx.author,
            reason=note,
            guild_id=ctx.guild.id,
            created_at=ctx.message.created_at,
        )
        await action.execute(ctx)
        await ctx.send(f"Added a note to **{target}**'s history (Case #{action._id}).", ephemeral=True)

    @commands.hybrid_command()
    @commands.guild_only()
    @checks.is_trial_moderator()
    async def kick(self, ctx, target: discord.Member, *, reason):
        """Kicks a member from the server.

        You must have the Trial Moderator role to use this.
        """

        if any(role.id in constants.TRIAL_MODERATOR_ROLES for role in getattr(target, "roles", [])):
            return await ctx.send("You can't punish that person!", ephemeral=True)

        action = Kick(
            target=target,
            user=ctx.author,
            reason=reason,
            guild_id=ctx.guild.id,
            created_at=ctx.message.created_at,
        )
        await action.notify()
        await action.execute(ctx)
        await ctx.send(f"Kicked **{target}** (Case #{action._id}).", ephemeral=True)

    @commands.hybrid_command(usage="<target> [expires_at] [reason]")
    @commands.guild_only()
    @checks.is_trial_moderator()
    async def ban(self, ctx, target: MemberOrIdConverter, *, time_and_reason):
        """Bans a member from the server.

        You must have the Trial Moderator role to use this.
        """

        if any(role.id in constants.TRIAL_MODERATOR_ROLES for role in getattr(target, "roles", [])):
            return await ctx.send("You can't punish that person!", ephemeral=True)

        expires_at, reason = await self.parse_time_and_reason(ctx, time_and_reason)

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
            await ctx.send(f"Banned **{target}** (Case #{action._id}).", ephemeral=True)
        else:
            await ctx.send(
                f"Banned **{target}** for **{time.human_timedelta(action.duration)}** (Case #{action._id}).",
                ephemeral=True,
            )

    @commands.hybrid_command()
    @commands.guild_only()
    @checks.is_trial_moderator()
    async def unban(self, ctx, target: BanConverter, *, reason=None):
        """Unbans a member from the server.

        You must have the Trial Moderator role to use this.
        """

        action = Unban(
            target=target.user,
            user=ctx.author,
            reason=reason,
            guild_id=ctx.guild.id,
        )
        await action.execute(ctx)
        await ctx.send(f"Unbanned **{target.user}** (Case #{action._id}).", ephemeral=True)

    @commands.hybrid_command(aliases=("mute",), usage="<target> [expires_at] [reason]")
    @commands.guild_only()
    @checks.is_trial_moderator()
    async def timeout(self, ctx, target: discord.Member, *, time_and_reason):
        """Places a member in timeout within the server.

        If duration is longer than 28 days, falls back to a mute.

        You must have the Trial Moderator role to use this.
        """

        if any(role.id in constants.TRIAL_MODERATOR_ROLES for role in getattr(target, "roles", [])):
            return await ctx.send("You can't punish that person!", ephemeral=True)

        expires_at, reason = await self.parse_time_and_reason(ctx, time_and_reason)

        if expires_at is None or expires_at > ctx.message.created_at + timedelta(days=28):
            action_cls = Mute
        else:
            action_cls = Timeout

        action = action_cls(
            target=target,
            user=ctx.author,
            reason=reason,
            guild_id=ctx.guild.id,
            created_at=ctx.message.created_at,
            expires_at=expires_at,
        )
        await action.execute(ctx)
        await action.notify()

        if action_cls is Timeout:
            await ctx.send(
                f"Placed **{target}** in timeout for **{time.human_timedelta(action.duration)}** (Case #{action._id}).",
                ephemeral=True,
            )
        elif action.duration is None:
            await ctx.send(f"Muted **{target}** (Case #{action._id}).", ephemeral=True)
        else:
            await ctx.send(
                f"Muted **{target}** for **{time.human_timedelta(action.duration)}** (Case #{action._id}).",
                ephemeral=True,
            )

    @commands.hybrid_command(aliases=("unmute",))
    @commands.guild_only()
    @checks.is_trial_moderator()
    async def untimeout(self, ctx, target: discord.Member, *, reason=None):
        """Removes a member from timeout within the server.

        If the member is muted, unmutes instead.

        You must have the Trial Moderator role to use this.
        """

        if any(x.name == "Muted" for x in target.roles):
            action_cls = Unmute
        else:
            action_cls = Untimeout

        action = action_cls(
            target=target,
            user=ctx.author,
            reason=reason,
            guild_id=ctx.guild.id,
        )
        await action.execute(ctx)
        await action.notify()

        if action_cls is Unmute:
            await ctx.send(f"Unmuted **{target}** (Case #{action._id}).", ephemeral=True)
        else:
            await ctx.send(f"Removed **{target}** from timeout (Case #{action._id}).", ephemeral=True)

    @commands.hybrid_command(name="setup-muted-role", aliases=("sync-muted-role",))
    @commands.guild_only()
    @checks.is_community_manager()
    async def setup_muted_role(self, ctx):
        """Sets up the Muted role's permissions.

        You must have the Community Manager role to use this.
        """

        role = discord.utils.get(ctx.guild.roles, name="Muted")
        if role is None:
            return await ctx.send("Please create a role named Muted first.", ephemeral=True)

        for channel in ctx.guild.channels:
            if isinstance(channel, discord.CategoryChannel) or not channel.permissions_synced:
                await channel.set_permissions(
                    role,
                    send_messages=False,
                    send_messages_in_threads=False,
                    add_reactions=False,
                    speak=False,
                    stream=False,
                )

        await ctx.send("I've set up permissions for the Muted role.")

    @commands.hybrid_command(aliases=("tmute",), usage="<target> [expires_at] [reason]")
    @checks.community_server_only()
    @checks.is_trial_moderator()
    async def tradingmute(self, ctx, target: discord.Member, *, time_and_reason):
        """Mutes a member in trading channels.

        You must have the Trial Moderator role to use this.
        """

        if any(role.id in constants.TRIAL_MODERATOR_ROLES for role in getattr(target, "roles", [])):
            return await ctx.send("You can't punish that person!")

        expires_at, reason = await self.parse_time_and_reason(ctx, time_and_reason)

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

    @commands.hybrid_command(aliases=("untradingmute", "tunmute", "untmute"))
    @checks.community_server_only()
    @checks.is_trial_moderator()
    async def tradingunmute(self, ctx, target: discord.Member, *, reason=None):
        """Unmutes a member in trading channels.

        You must have the Trial Moderator role to use this.
        """

        action = TradingUnmute(
            target=target,
            user=ctx.author,
            reason=reason,
            guild_id=ctx.guild.id,
        )
        await action.execute(ctx)
        await action.notify()
        await ctx.send(f"Unmuted **{target}** in trading channels (Case #{action._id}).", ephemeral=True)

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
        elif action.type == "timeout":
            action_type = SymbolicUntimeout
        elif action.type == "trading_mute":
            action_type = TradingUnmute
        elif action.type == "emergency_alert_ban":
            action_type = EmergencyAlertUnban
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

        await self.bot.mongo.db.action.update_one({"_id": raw_action["_id"]}, {"$set": {"resolved": True}})

    @tasks.loop(seconds=30)
    async def check_actions(self):
        await self.bot.wait_until_ready()
        query = {"resolved": False, "expires_at": {"$lt": datetime.now(timezone.utc)}}

        async for action in self.bot.mongo.db.action.find(query):
            self.bot.loop.create_task(self.reverse_raw_action(action))

    @commands.hybrid_group(aliases=("his",), fallback="list")
    @commands.guild_only()
    @checks.is_trial_moderator()
    async def history(self, ctx, *, target: FetchUserConverter):
        """Views a member's punishment history.

        You must have the Trial Moderator role to use this.
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
            return {"name": name, "value": "\n".join(lines)[:1024], "inline": False}

        pages = ViewMenuPages(
            source=AsyncEmbedFieldsPageSource(
                get_actions(),
                title=f"Punishment History • {target} ({target.id})",
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
    @checks.is_trial_moderator()
    async def delete(self, ctx, ids: commands.Greedy[int]):
        """Deletes one or more entries from punishment history.

        You must have the Trial Moderator role to use this.
        """

        result = await self.bot.mongo.db.action.delete_many({"_id": {"$in": ids}, "guild_id": ctx.guild.id})
        word = "entry" if result.deleted_count == 1 else "entries"
        await ctx.send(f"Successfully deleted {result.deleted_count} {word}.")

    @history.command(name="note")
    @commands.guild_only()
    @checks.is_trial_moderator()
    async def history_note(self, ctx, id: int, *, note: Optional[str] = ""):
        """Adds a note to an entry from punishment history.

        You must have the Trial Moderator role to use this.
        """

        note = with_attachment_urls(note, ctx.message.attachments)

        if len(note) == 0:
            return await ctx.send_help(ctx.command)
        elif len(note) > constants.EMBED_FIELD_CHAR_LIMIT:
            return await ctx.send(f"History notes (including attachment URLs) can be at most {constants.EMBED_FIELD_CHAR_LIMIT} characters.")

        reset = note.lower() == "reset"
        
        result = await self.bot.mongo.db.action.find_one_and_update(
            {"_id": id, "guild_id": ctx.guild.id},
            {"$set": {"note": note}} if not reset else {"$unset": {"note": 1}},
        )
        if result is None:
            return await ctx.send("Could not find an entry with that ID.", ephemeral=True)

        action = Action.build_from_mongo(self.bot, result)
        await ctx.send(
            f"Successfully added a note to entry **{id}**." if not reset else f"Successfully removed note of entry **{id}**.",
            embed=action.to_info_embed(),
            ephemeral=True,
        )

    @history.command(aliases=("show",))
    @commands.guild_only()
    @checks.is_trial_moderator()
    async def info(self, ctx, id: int):
        """Shows an entry from punishment history.

        You must have the Trial Moderator role to use this.
        """

        action = await self.bot.mongo.db.action.find_one({"_id": id, "guild_id": ctx.guild.id})
        if action is None:
            return await ctx.send("Could not find an entry with that ID.", ephemeral=True)

        action = Action.build_from_mongo(self.bot, action)
        await ctx.send(embed=action.to_info_embed())

    async def cog_unload(self):
        self.check_actions.cancel()


async def setup(bot):
    await bot.add_cog(Moderation(bot))
