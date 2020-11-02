from helpers.pagination import AsyncListPageSource
import discord
from discord.ext import commands, menus


class SpeciesConverter(commands.Converter):
    async def convert(self, ctx, arg):
        if arg.startswith("#") and arg[1:].isdigit():
            arg = arg[1:]

        if arg.isdigit():
            species = ctx.bot.data.species_by_number(int(arg))
        else:
            species = ctx.bot.data.species_by_name(arg)

        if species is None:
            raise ValueError(f"Could not find a pokémon matching `{arg}`.")
        return species


class Collectors(commands.Cog):
    """For collectors."""

    def __init__(self, bot):
        self.bot = bot

    async def doc_to_species(self, doc):
        for x in doc.keys():
            if x != "_id":
                yield self.bot.data.species_by_number(int(x))

    @commands.group(aliases=("col",), invoke_without_command=True)
    async def collect(self, ctx, *, member: discord.Member = None):
        """Allows members to keep track of the collectors for a pokémon species.

        If no subcommand is called, lists the pokémon collected by you or someone else.
        """

        if member is None:
            member = ctx.author

        result = await self.bot.db.collector.find_one({"_id": member.id})

        pages = menus.MenuPages(
            source=AsyncListPageSource(
                self.doc_to_species(result or {}),
                title=str(ctx.author),
                format_item=lambda x: x.name,
            )
        )

        try:
            await pages.start(ctx)
        except IndexError:
            await ctx.send("No pokémon found.")

    @collect.command()
    async def add(self, ctx, *, species: SpeciesConverter):
        """Adds a pokémon species to your collecting list."""

        result = await self.bot.db.collector.update_one(
            {"_id": ctx.author.id},
            {"$set": {str(species.id): True}},
            upsert=True,
        )

        if result.upserted_id or result.modified_count > 0:
            return await ctx.send(f"Added **{species}** to your collecting list.")
        else:
            return await ctx.send(f"**{species}** is already on your collecting list!")

    @collect.command()
    async def remove(self, ctx, *, species: SpeciesConverter):
        """Remove a pokémon species from your collecting list."""

        result = await self.bot.db.collector.update_one(
            {"_id": ctx.author.id},
            {"$unset": {str(species.id): 1}},
        )

        if result.modified_count > 0:
            return await ctx.send(f"Removed **{species}** from your collecting list.")
        else:
            return await ctx.send(f"**{species}** is not on your collecting list!")

    @collect.command()
    async def search(self, ctx, *, species: SpeciesConverter):
        """Lists the collectors of a pokémon species."""

        users = self.bot.db.collector.find({str(species.id): True})
        pages = menus.MenuPages(
            source=AsyncListPageSource(
                users,
                title=str(species),
                format_item=lambda x: f"<@{x['_id']}>",
            )
        )

        try:
            await pages.start(ctx)
        except IndexError:
            await ctx.send("No users found.")

    @commands.command()
    async def collectors(self, ctx, *, species: SpeciesConverter):
        """An alias for the collect search command."""

        await ctx.invoke(self.search, species=species)


def setup(bot):
    bot.add_cog(Collectors(bot))
