import discord
from discord.ext import commands


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
