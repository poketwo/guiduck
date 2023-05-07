import discord
from discord.ext import commands

from helpers import checks


class ReactionRoles(commands.Cog):
    """For adding roles utility."""

    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_group()
    @commands.guild_only()
    @checks.is_community_manager()
    async def rolemenu(self, ctx):
        """Utilities for reaction role menus."""

        await ctx.send_help(ctx.command)

    async def get_menu(self, name, guild):
        return await self.bot.mongo.db.rolemenu.find_one({"name": name, "guild_id": guild.id})

    async def menu_from_payload(self, payload):
        return await self.bot.mongo.db.rolemenu.find_one(
            {
                "_id": payload.message_id,
                "channel_id": payload.channel_id,
                "guild_id": payload.guild_id,
            }
        )

    @rolemenu.command(name="create")
    @commands.guild_only()
    @checks.is_community_manager()
    async def create(self, ctx, message: discord.Message, *, name):
        """Creates a role menu on a certain message.

        You must have the Community Manager role to use this.
        """

        if message.guild.id != ctx.guild.id:
            return await ctx.send("Cannot create role menu in different guild.", ephemeral=True)

        await self.bot.mongo.db.rolemenu.insert_one(
            {
                "_id": message.id,
                "channel_id": message.channel.id,
                "guild_id": message.guild.id,
                "options": {},
                "name": name,
            }
        )
        await ctx.send(f"Created role menu in {message.channel.mention}.")

    @rolemenu.command(name="list")
    @commands.guild_only()
    @checks.is_community_manager()
    async def list(self, ctx):
        """Lists this server's role menus.

        You must have the Community Manager role to use this.
        """

        rr = await self.bot.mongo.db.rolemenu.find({"guild_id": ctx.guild.id}).to_list(None)
        await ctx.send(f"Role Menus:\n\n" + "\n".join(f"**{r['name']}**" for r in rr))

    @rolemenu.command(name="delete")
    @commands.guild_only()
    @checks.is_community_manager()
    async def delete(self, ctx, name):
        """Deletes an existing role menu.

        You must have the Community Manager role to use this.
        """

        result = await self.bot.mongo.db.rolemenu.delete_one({"name": name, "guild_id": ctx.guild.id})
        if result.deleted_count > 0:
            await ctx.send(f"Deleted role menu **{name}**.")
        else:
            await ctx.send("Could not find role menu with that name.", ephemeral=True)

    @rolemenu.command(name="view")
    @commands.guild_only()
    @checks.is_community_manager()
    async def view(self, ctx, name):
        """Shows information about a role menu.

        You must have the Community Manager role to use this.
        """

        obj = await self.get_menu(name, ctx.guild)
        if obj is None:
            return await ctx.send("Could not find role menu with that name.", ephemeral=True)

        options = obj["options"].items()
        message = []

        for emoji, role in options:
            role = ctx.guild.get_role(role)
            try:
                # Custom emoji
                emoji = self.bot.get_emoji(int(emoji))
            except ValueError:
                pass
            message.append(f"{emoji} for **{role}**")

        await ctx.send(f"Role Menu **{name}**\n\n" + "\n".join(message))

    @rolemenu.command(name="add")
    @commands.guild_only()
    @checks.is_community_manager()
    async def add(self, ctx, name, emoji, role: discord.Role):
        """Adds an emoji and role to a role menu.

        You must have the Community Manager role to use this.
        """

        menu = await self.get_menu(name, ctx.guild)
        if menu is None:
            return await ctx.send("Could not find role menu with that name.", ephemeral=True)

        try:
            # Custom emoji
            emoji = await commands.EmojiConverter().convert(ctx, emoji)
        except commands.BadArgument:
            pass

        message = await self.bot.get_channel(menu["channel_id"]).fetch_message(menu["_id"])

        try:
            await message.add_reaction(emoji)
        except discord.InvalidArgument:
            return await ctx.send("Please enter a valid emoji.", ephemeral=True)

        key = str(emoji.id) if isinstance(emoji, discord.Emoji) else emoji
        await self.bot.mongo.db.rolemenu.update_one({"_id": menu["_id"]}, {"$set": {f"options.{key}": role.id}})
        await ctx.send(f"Added {emoji} linking to role **{role}** to role menu in {message.channel.mention}.")

    @rolemenu.command(name="remove")
    @commands.guild_only()
    @checks.is_community_manager()
    async def rolemenu_remove(self, ctx, name, emoji):
        """Removes an emoji and role from a role menu.

        You must have the Community Manager role to use this.
        """

        menu = await self.get_menu(name, ctx.guild)
        if menu is None:
            return await ctx.send("Could not find role menu with that name.", ephemeral=True)

        try:
            # Custom emoji
            emoji = await commands.EmojiConverter().convert(ctx, emoji)
        except commands.BadArgument:
            pass

        message = await self.bot.get_channel(menu["channel_id"]).fetch_message(menu["_id"])

        try:
            await message.clear_reaction(emoji)
        except discord.InvalidArgument:
            return await ctx.send("Please enter a valid emoji.", ephemeral=True)

        key = str(emoji.id) if isinstance(emoji, discord.Emoji) else emoji
        role = ctx.guild.get_role(menu["options"][key])
        await self.bot.mongo.db.rolemenu.update_one({"_id": menu["_id"]}, {"$unset": {f"options.{key}": 1}})
        await ctx.send(f"Removed {emoji} linking to role **{role}** to role menu in {message.channel.mention}.")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if payload.user_id == self.bot.user.id:
            return

        menu = await self.menu_from_payload(payload)
        if menu is None:
            return

        emoji = str(payload.emoji.id) if payload.emoji.is_custom_emoji() else payload.emoji.name
        if emoji in menu["options"]:
            guild = self.bot.get_guild(payload.guild_id)
            role = guild.get_role(menu["options"][emoji])
            member = guild.get_member(payload.user_id)
            await member.add_roles(role)
            await member.send(f"Gave you the **{role}** role!")

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        if payload.user_id == self.bot.user.id:
            return

        menu = await self.menu_from_payload(payload)
        if menu is None:
            return

        emoji = str(payload.emoji.id) if payload.emoji.is_custom_emoji() else payload.emoji.name
        if emoji in menu["options"]:
            guild = self.bot.get_guild(payload.guild_id)
            role = guild.get_role(menu["options"][emoji])
            member = guild.get_member(payload.user_id)
            await member.remove_roles(role)
            await member.send(f"Took away the **{role}** role!")


async def setup(bot):
    await bot.add_cog(ReactionRoles(bot))
