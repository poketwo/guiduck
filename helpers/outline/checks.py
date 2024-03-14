from discord.ext import commands

from helpers.context import GuiduckContext
from helpers import checks
from .converters import CollectionConverter
from .exceptions import MissingCommandPermission, EphemeralRequired


def has_outline_access():
    """Check if user has perms to use Outline things"""

    async def predicate(ctx):
        accessible_collections = await CollectionConverter.get_user_collections(ctx)
        if len(accessible_collections) == 0:
            raise MissingCommandPermission
        return True

    return commands.check(predicate)


async def do_ephemeral(ctx: GuiduckContext):
    ephemeral_arg = (ctx.interaction.namespace if ctx.interaction else list(ctx.kwargs.values())[0]).ephemeral

    if await checks.passes_check(checks.staff_categories_only, ctx):
        ephemeral = False
    else:
        if ctx.interaction:
            ephemeral = True  # Force ephemeral incase it's outside staff categories if app command
        else:
            raise EphemeralRequired

    return ephemeral or ephemeral_arg
