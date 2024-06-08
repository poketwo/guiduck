from datetime import datetime, timedelta, timezone
from discord.ext import commands

from . import constants
from . import time


class NotInGuild(commands.CheckFailure):
    pass


class EmergencyAlertBanned(commands.CheckFailure):
    pass


def is_community_manager():
    return commands.check_any(commands.is_owner(), commands.has_any_role(*constants.COMMUNITY_MANAGER_ROLES))


def is_moderator():
    return commands.check_any(commands.is_owner(), commands.has_any_role(*constants.MODERATOR_ROLES))


def is_trial_moderator():
    return commands.check_any(commands.is_owner(), commands.has_any_role(*constants.TRIAL_MODERATOR_ROLES))


def in_guilds(*guild_ids):
    def predicate(ctx):
        if ctx.guild is None or ctx.guild.id not in guild_ids:
            raise NotInGuild("This command is not available in this guild.")
        return True

    return commands.check(predicate)


def community_server_only():
    return in_guilds(constants.COMMUNITY_SERVER_ID)


def support_server_only():
    return in_guilds(constants.SUPPORT_SERVER_ID)


def is_level(level):
    async def predicate(ctx):
        user = await ctx.bot.mongo.db.member.find_one(
            {"_id": {"id": ctx.author.id, "guild_id": ctx.guild.id}}, {"level": 1}
        )
        return user.get("level", 0) >= level

    return commands.check(predicate)


def is_not_emergency_alert_banned():
    async def predicate(ctx):
        member = await ctx.bot.mongo.db.member.find_one({"_id": {"id": ctx.author.id, "guild_id": ctx.guild.id}})
        now = datetime.now(timezone.utc)

        permanently_banned = member.get("emergency_alert_banned")
        if permanently_banned:
            ctx.command.reset_cooldown(ctx)
            raise EmergencyAlertBanned(
                "You've been permanently banned from issuing emergency staff alerts due to violation(s) of its rules. If you think that this was a mistake, please contact a staff member."
            )

        temp_banned_until = member.get("emergency_alert_banned_until")
        temp_banned = temp_banned_until is not None
        if temp_banned and temp_banned_until > now:
            ctx.command.reset_cooldown(ctx)
            duration_seconds = (temp_banned_until - now).total_seconds()
            raise EmergencyAlertBanned(
                f"You've been banned from issuing emergency staff alerts for **{time.human_timedelta(timedelta(seconds=duration_seconds))}** due to violation(s) of its rules."
            )
        return True

    return commands.check(predicate)
