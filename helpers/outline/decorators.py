import functools
from typing import Awaitable, Callable, Optional

import discord
from discord.ext import commands
from discord.utils import maybe_coroutine

from helpers.context import GuiduckContext


def with_typing(do_ephemeral: Optional[Callable[[GuiduckContext], Awaitable[bool] | bool]] = None):
    """Run command with calling ctx.typing, and make it ephemeral depending on check"""

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            ctx = discord.utils.find(lambda a: isinstance(a, commands.Context), args)
            ephemeral = do_ephemeral and await maybe_coroutine(do_ephemeral, ctx)

            try:
                async with ctx.typing(ephemeral=ephemeral):
                    return await func(*args, **kwargs)
            except discord.InteractionResponded:
                return await func(*args, **kwargs)

        return wrapper

    return decorator


def remedy_args_bug(func):
    """This decorator is to remedy a bug in discord.py (https://github.com/Rapptz/discord.py/issues/9641)
    that makes it so that callable default values of flags aren't called in case of slash commands. So this decorator
    sets these for the FlagConverter object before the command's callback is ran."""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        ctx = discord.utils.find(lambda a: isinstance(a, GuiduckContext), args)
        flags = discord.utils.find(lambda k: isinstance(k, commands.FlagConverter), kwargs.values())

        if flags is not None:
            for flag in flags.get_flags().values():
                arg = getattr(flags, flag.attribute)
                if callable(arg):
                    setattr(flags, flag.attribute, await discord.utils.maybe_coroutine(arg, ctx))

        return await func(*args, **kwargs)

    return wrapper
