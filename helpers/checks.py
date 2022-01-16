from discord.ext import commands

from . import constants


def is_community_manager():
    return commands.has_any_role(*constants.COMMUNITY_MANAGER_ROLES)


def is_moderator():
    return commands.has_any_role(*constants.MODERATOR_ROLES)


def in_guilds(*guild_ids):
    def predicate(ctx):
        return ctx.guild is not None and ctx.guild.id in guild_ids

    return commands.check(predicate)


def community_server_only():
    return in_guilds(constants.COMMUNITY_SERVER_ID)


def support_server_only():
    return in_guilds(constants.SUPPORT_SERVER_ID)
