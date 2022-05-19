from discord.ext import commands

from . import constants


class NotInGuild(commands.CheckFailure):
    pass


def is_community_manager():
    return commands.has_any_role(*constants.COMMUNITY_MANAGER_ROLES)


def is_moderator():
    return commands.has_any_role(*constants.COMMUNITY_MANAGER_ROLES, *constants.MODERATOR_ROLES)


def is_trial_moderator():
    return commands.has_any_role(
        *constants.COMMUNITY_MANAGER_ROLES,
        *constants.MODERATOR_ROLES,
        *constants.TRIAL_MODERATOR_ROLES,
    )


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
