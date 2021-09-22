import discord
from discord.ext import commands
from discord.ext.menus.views import ViewMenuPages
from helpers.pagination import AsyncEmbedListPageSource


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


class Collectors(commands.Cog):
    """For collectors."""

    def __init__(self, bot):
        self.bot = bot

    async def doc_to_species(self, doc):
        for x in doc.keys():
            if x == "_id":
                continue
            yield self.bot.data.species_by_number(int(x))

    @commands.group(aliases=("col",), invoke_without_command=True)
    async def collect(self, ctx, *, member: discord.Member = None):
        """Allows members to keep track of the collectors for a pokémon species.

        If no subcommand is called, lists the pokémon collected by you or someone else.
        """

        if member is None:
            member = ctx.author

        result = await self.bot.mongo.db.collector.find_one({"_id": member.id})

        pages = ViewMenuPages(
            source=AsyncEmbedListPageSource(
                self.doc_to_species(result or {}),
                title=str(member),
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

        result = await self.bot.mongo.db.collector.update_one(
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

        result = await self.bot.mongo.db.collector.update_one(
            {"_id": ctx.author.id},
            {"$unset": {str(species.id): 1}},
        )

        if result.modified_count > 0:
            return await ctx.send(f"Removed **{species}** from your collecting list.")
        else:
            return await ctx.send(f"**{species}** is not on your collecting list!")

    @collect.command()
    async def clear(self, ctx):
        """Clear your collecting list."""

        await self.bot.mongo.db.collector.delete_one({"_id": ctx.author.id})
        await ctx.send("Cleared your collecting list.")

    async def query_collectors(self, species):
        async for x in self.bot.mongo.db.collector.find({str(species.id): True}):
            user = self.bot.get_user(x["_id"])
            if user is None:
                continue
            yield user

    @collect.command()
    async def search(self, ctx, *, species: SpeciesConverter):
        """Lists the collectors of a pokémon species."""

        def format_item(user):
            return f"{user} {user.mention} `<@{user.id}>`"

        pages = ViewMenuPages(
            source=AsyncEmbedListPageSource(
                self.query_collectors(species),
                title=str(species),
                format_item=format_item,
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
