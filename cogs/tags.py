from dataclasses import dataclass

import discord
import pymongo
from bson.objectid import ObjectId
from discord.ext import commands, menus
from helpers.pagination import AsyncListPageSource


@dataclass
class Tag:
    name: str
    owner_id: int
    alias: bool
    uses: int = 0
    content: str = None
    original: str = None
    _id: ObjectId = None

    @property
    def id(self):
        return self._id

    def to_dict(self):
        base = dict(
            name=self.name,
            owner_id=self.owner_id,
            alias=self.alias,
            uses=self.uses,
        )
        if self.alias:
            base["original"] = self.original
        else:
            base["content"] = self.content
        return base


class FakeUser(discord.Object):
    @property
    def avatar_url(self):
        return "https://cdn.discordapp.com/embed/avatars/0.png"

    @property
    def mention(self):
        return "<@{0.id}>".format(self)

    def __str__(self):
        return str(self.id)


class Tags(commands.Cog):
    """For tags."""

    def __init__(self, bot):
        self.bot = bot

    async def get_tag(self, name, original=False):
        tag_data = await self.bot.db.tag.find_one({"name": name})
        if tag_data is None:
            return None
        tag = Tag(**tag_data)

        if tag.alias and original:
            return await self.get_tag(tag.original)

        return tag

    async def query_tags(self, query, sort=True):
        tags = self.bot.db.tag.find(query)
        if sort:
            tags = tags.sort("name", 1)
        async for tag_data in tags:
            yield Tag(**tag_data)

    async def send_tags(self, ctx, tags):
        pages = menus.MenuPages(
            source=AsyncListPageSource(
                tags,
                show_index=True,
                format_item=lambda x: x.name,
            )
        )

        try:
            await pages.start(ctx)
        except IndexError:
            await ctx.send("No tags found.")

    # Reading tags

    @commands.group(invoke_without_command=True)
    async def tag(self, ctx, *, name):
        """Allows you to save text into tags for easy access.

        If no subcommand is called, this will search for the requested tag.
        """

        tag = await self.get_tag(name, original=True)
        if tag is None:
            return await ctx.send("Tag not found.")

        await ctx.send(tag.content)
        await self.bot.db.tag.update_one({"_id": tag.id}, {"$inc": {"uses": 1}})

    @tag.command()
    async def info(self, ctx, *, name):
        """Retrieves info about a tag."""

        tag = await self.get_tag(name)
        if tag is None:
            return await ctx.send("Tag not found.")

        user = self.bot.get_user(tag.owner_id)
        if user is None:
            user = FakeUser(tag.owner_id)

        embed = discord.Embed(color=discord.Color.blurple(), title=tag.name)
        embed.add_field(name="Owner", value=user.mention)
        if tag.alias:
            embed.add_field(name="Original", value=tag.original)
        else:
            embed.add_field(name="Uses", value=tag.uses)
        embed.set_author(name=str(user), icon_url=user.avatar_url)

        await ctx.send(embed=embed)

    @tag.command()
    async def raw(self, ctx, *, name):
        """Gets the raw content of the tag, with markdown escaped."""

        tag = await self.get_tag(name, original=True)
        first_step = discord.utils.escape_markdown(tag.content)
        await ctx.send(first_step.replace("<", "\\<"))

    # Searching tags

    @tag.command()
    async def all(self, ctx):
        """Lists all tags."""

        await self.send_tags(ctx, self.query_tags({}))

    @tag.command()
    async def search(self, ctx, *, text):
        """Searches for a tag."""

        await self.send_tags(
            ctx, self.query_tags({"$text": {"$search": text}}, sort=False)
        )

    @tag.command()
    async def list(self, ctx, *, member: discord.Member = None):
        """Lists all the tags that belong to you or someone else."""

        if member is None:
            member = ctx.author
        await self.send_tags(ctx, self.query_tags({"owner_id": member.id}))

    # Writing tags

    @tag.command()
    async def create(self, ctx, name, *, content):
        """Creates a new tag owned by you."""

        tag = Tag(name=name, owner_id=ctx.author.id, alias=False, content=content)
        try:
            await self.bot.db.tag.insert_one(tag.to_dict())
            await ctx.send(f'Tag "{tag.name}" successfully created.')
        except pymongo.errors.DuplicateKeyError:
            await ctx.send(f'A tag with the name "{tag.name}" already exists.')

    @tag.command()
    async def alias(self, ctx, name, *, original):
        """Creates an alias for a pre-existing tag."""

        original = await self.get_tag(original, original=True)
        if original is None:
            return await ctx.send("A tag with that name does not exist.")

        tag = Tag(name=name, owner_id=ctx.author.id, alias=True, original=original.name)
        try:
            await self.bot.db.tag.insert_one(tag.to_dict())
            await ctx.send(
                f'Tag alias "{tag.name}" pointing to "{original.name}" successfully created.'
            )
        except pymongo.errors.DuplicateKeyError:
            await ctx.send(f'A tag with the name "{tag.name}" already exists.')

    @tag.command()
    async def edit(self, ctx, name, *, content):
        """Modifies an existing tag that you own."""

        tag = await self.get_tag(name)
        if tag is None:
            return await ctx.send("Tag not found.")
        if tag.owner_id != ctx.author.id:
            return await ctx.send("You do not own that tag.")
        if tag.alias:
            return await ctx.send("You cannot edit an alias.")

        await self.bot.db.tag.update_one(
            {"_id": tag.id}, {"$set": {"content": content}}
        )
        await ctx.send(f"Successfully edited tag.")

    @tag.command()
    async def delete(self, ctx, name):
        """Removes a tag that you own."""

        tag = await self.get_tag(name)
        if tag is None:
            return await ctx.send("Tag not found.")
        if tag.owner_id != ctx.author.id:
            return await ctx.send("You do not own that tag.")

        await self.bot.db.tag.delete_one({"_id": tag.id})
        await self.bot.db.tag.delete_many({"original": tag.name})
        await ctx.send(f"Tag and corresponding aliases successfully deleted.")


def setup(bot):
    bot.add_cog(Tags(bot))
