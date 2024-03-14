from discord.ext import commands

from . import constants


class NotInGuild(commands.CheckFailure):
    pass


def is_admin():
    return commands.check_any(commands.is_owner(), commands.has_any_role(*constants.ADMIN_ROLES))


def is_community_manager():
    return commands.check_any(commands.is_owner(), commands.has_any_role(*constants.COMMUNITY_MANAGER_ROLES))


def is_moderator():
    return commands.check_any(commands.is_owner(), commands.has_any_role(*constants.MODERATOR_ROLES))


def is_trial_moderator():
    return commands.check_any(commands.is_owner(), commands.has_any_role(*constants.TRIAL_MODERATOR_ROLES))


def is_developer():
    return commands.check_any(commands.is_owner(), commands.has_role(constants.DEVELOPER_ROLE))


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


class NotInCategory(commands.CheckFailure):
    pass


def in_categories(*category_ids):
    def predicate(ctx):
        if ctx.channel.category is None or ctx.channel.category.id not in category_ids:
            raise NotInCategory("This command is restricted to specific categories.")
        return True

    return commands.check_any(is_admin(), commands.check(predicate))


def staff_categories_only():
    return in_categories(
        717881335313858610,
        1122578424867864616,
        1196191915272577134,
        730992224635977748,
        1103248448934912052,
        1104727423603449866,
    )


def is_level(level):
    async def predicate(ctx):
        user = await ctx.bot.mongo.db.member.find_one(
            {"_id": {"id": ctx.author.id, "guild_id": ctx.guild.id}}, {"level": 1}
        )
        return user.get("level", 0) >= level

    return commands.check(predicate)
