from discord.ext import commands

COMMUNITY_MANAGER_ROLE = 718006431231508481
MODERATOR_ROLES = (718006431231508481, 724879492622843944, 813433839471820810)


def is_community_manager():
    return commands.has_role(COMMUNITY_MANAGER_ROLE)


def is_moderator():
    return commands.has_any_role(*MODERATOR_ROLES)


def in_guilds(*guild_ids):
    def predicate(ctx):
        return ctx.guild is not None and ctx.guild.id in guild_ids

    return commands.check(predicate)


def community_server_only():
    return in_guilds(716390832034414685)


def support_server_only():
    return in_guilds(930339868503048202)
