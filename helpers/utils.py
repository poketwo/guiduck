from datetime import datetime
from typing import Iterable, List, NamedTuple

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


class MemberOrFetchUserConverter(commands.Converter):
    async def convert(self, ctx, arg):
        try:
            return await commands.MemberConverter().convert(ctx, arg)
        except commands.MemberNotFound:
            return await FetchUserConverter().convert(ctx, arg)


def with_attachment_urls(content: str, attachments: Iterable[discord.Attachment]) -> str:
    for attachment in attachments:
        content += f"\n{attachment.url}"
    return content


def full_format_dt(dt: datetime) -> str:
    """Formats datetime object to discord timestamp in `FULL (RELATIVE)` format"""

    return f"{discord.utils.format_dt(dt)} ({discord.utils.format_dt(dt, 'R')})"


def get_substring_matches(substring: str, strings: List[str]) -> List[str]:
    """
    Takes in a substring and a list of strings and returns those strings which have substring in it,
    sorted by where in the string it appears.
    """

    matches = []
    for string in strings:
        if substring in string:
            matches.append(string)

    matches.sort(key=lambda c: c.index(substring))
    return matches
