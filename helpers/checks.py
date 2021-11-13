from discord.ext import commands

COMMUNITY_MANAGER_ROLE = 718006431231508481
MODERATOR_ROLES = (718006431231508481, 724879492622843944, 813433839471820810)


def is_community_manager():
    return commands.has_role(COMMUNITY_MANAGER_ROLE)


def is_moderator():
    return commands.has_any_role(*MODERATOR_ROLES)
