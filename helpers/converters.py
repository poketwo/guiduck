import contextlib
from datetime import datetime
from typing import List, Optional
import discord
from discord.ext import commands

from helpers import constants
from helpers.context import GuiduckContext


class SpeciesConverter(commands.Converter):
    async def convert(self, ctx, arg):
        if arg.startswith("#") and arg[1:].isdigit():
            arg = arg[1:]

        if arg.isdigit():
            species = ctx.bot.data.species_by_number(int(arg))
        else:
            species = ctx.bot.data.species_by_name(arg)

        if species is None:
            raise commands.BadArgument(f"Could not find a pokémon matching `{arg}`.")
        return species


class FetchChannelOrThreadConverter(commands.Converter):
    async def convert(self, ctx, arg):
        try:
            return await commands.GuildChannelConverter().convert(ctx, arg)
        except commands.ChannelNotFound:
            try:
                return await commands.ThreadConverter().convert(ctx, arg)
            except commands.ThreadNotFound:
                try:
                    return await ctx.bot.fetch_channel(int(arg))
                except (discord.NotFound, discord.HTTPException, ValueError):
                    raise commands.ChannelNotFound(arg)


class MonthConverter(commands.Converter):
    async def convert(self, ctx: GuiduckContext, argument: Optional[str] = None) -> int:
        now = discord.utils.utcnow()
        if not argument:
            return now.month

        if argument.isdigit():
            result = int(argument)
            if result < 1 or result > 12:
                raise ValueError("Month number can be 1-12")

            return result

        dt = None
        for spec in ["%B", "%b"]:
            with contextlib.suppress(ValueError):
                dt = datetime.strptime(argument, spec)
            if dt:
                break

        if not dt:
            raise ValueError("Invalid month provided")

        return dt.month


class GreedyMemberOrUserConverter(commands.Converter):
    async def convert(self, ctx: GuiduckContext, argument: str) -> List[discord.User | discord.Member]:
        words = argument.split()

        results = []
        start = 0
        end = 1
        while start < len(words):
            sub = " ".join(words[start:end])

            result = None
            try:
                result = await commands.MemberConverter().convert(ctx, sub)
            except commands.MemberNotFound:
                try:
                    result = await commands.UserConverter().convert(ctx, sub)
                except commands.UserNotFound:
                    pass

            if result:
                results.append(result)
                start = end
                end += 1
            else:
                if end >= len(words):
                    start += 1
                    end = start + 1
                else:
                    end += 1

        return results


class ActivityArgs(commands.FlagConverter):
    """Date flags for activity command"""

    role: Optional[discord.Role] = commands.flag(
        positional=True,
        description="The role whose members' activity to show",
        max_args=1,
        default=None,
    )
    users: GreedyMemberOrUserConverter = commands.flag(
        aliases=("user", "u", "members", "member"),
        description="The users whose activities to show",
        max_args=1,
        default=None,
    )
    show_all: Optional[bool] = commands.flag(
        name="all-users",
        aliases=("all", "all-agents", "all-members"),
        description="Whether or not to show all agents in the database regardless of role",
        max_args=1,
        default=False,
    )
    show_ids: Optional[bool] = commands.flag(
        name="show-ids",
        aliases=("ids", "id", "show-id"),
        description="Whether or not to show user IDs instead of names.",
        max_args=1,
        default=False,
    )
    month: MonthConverter = commands.flag(
        aliases=("m", "mo"),
        description="The month",
        max_args=1,
        default=None,
    )
    year: int = commands.flag(
        aliases=("y",),
        description="The year",
        max_args=1,
        default=None,
    )
