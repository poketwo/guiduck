from typing import NamedTuple

import discord
from discord.ext import commands


class FakeAvatar(NamedTuple):
    url: str


class FakeUser(discord.Object):
    @property
    def avatar(self):
        return None

    @property
    def default_avatar(self):
        return FakeAvatar("https://cdn.discordapp.com/embed/avatars/0.png")

    @property
    def display_avatar(self):
        return self.default_avatar

    @property
    def mention(self):
        return "<@{0.id}>".format(self)

    @property
    def guild_permissions(self):
        return discord.Permissions.none()

    def __str__(self):
        return str(self.id)

    async def send(self, *args, **kwargs):
        pass

    async def add_roles(self, *args, **kwargs):
        pass

    async def remove_roles(self, *args, **kwargs):
        pass


class FetchUserConverter(commands.Converter):
    async def convert(self, ctx, arg):
        try:
            return await commands.UserConverter().convert(ctx, arg)
        except commands.UserNotFound:
            pass

        try:
            return await ctx.bot.fetch_user(int(arg))
        except (discord.NotFound, discord.HTTPException, ValueError):
            raise commands.UserNotFound(arg)
