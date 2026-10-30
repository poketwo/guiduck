from contextlib import suppress
from datetime import datetime
import io
from textwrap import shorten
from typing import Iterable, List, NamedTuple, Optional

import discord
from discord.ext import commands

from . import constants, time


async def log_staff_action(ctx, title, *, fields=(), files=()):
    channel = ctx.bot.get_channel(constants.STAFF_LOGS_CHANNEL_ID)
    if channel is None:
        return

    embed = discord.Embed(color=discord.Color.blurple(), title=title)
    embed.set_author(name=str(ctx.author), icon_url=ctx.author.display_avatar.url)
    embed.add_field(name="Moderator", value=f"{ctx.author.mention} ({ctx.author.id})", inline=False)
    for name, value in fields:
        embed.add_field(name=name, value=value, inline=False)
    embed.timestamp = discord.utils.utcnow()

    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Jump", url=ctx.message.jump_url))

    files = [discord.File(io.BytesIO(content.encode()), filename=filename) for filename, content in files]
    await channel.send(embed=embed, files=files, view=view, allowed_mentions=discord.AllowedMentions.none())


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


class CachedUser(FakeUser):
    """A user built from the `member` collection, for members not in the gateway cache."""

    def __init__(self, data):
        super().__init__(data["_id"]["id"])
        self.name = data.get("name") or str(self.id)
        self.discriminator = data.get("discriminator") or "0"
        self.nick = data.get("nick")
        self.avatar_url = data.get("avatar")

    @property
    def display_name(self):
        return self.nick or self.name

    @property
    def display_avatar(self):
        return FakeAvatar(self.avatar_url) if self.avatar_url else self.default_avatar

    def __str__(self):
        return self.name if self.discriminator == "0" else f"{self.name}#{self.discriminator}"


async def resolve_users(bot, guild_id, user_ids, *, fetch=False):
    """Maps user IDs to the best available user object without needing the Members intent.

    Tries the gateway cache, then one query on the `member` collection, then (if `fetch`) the API.
    """

    guild = bot.get_guild(guild_id)
    users = {}
    for user_id in set(user_ids):
        if user := (guild and guild.get_member(user_id)) or bot.get_user(user_id):
            users[user_id] = user

    missing = [x for x in set(user_ids) if x not in users]
    if missing:
        query = {"_id": {"$in": [{"id": x, "guild_id": guild_id} for x in missing]}}
        async for data in bot.mongo.db.member.find(query, {"name": 1, "discriminator": 1, "nick": 1, "avatar": 1}):
            users[data["_id"]["id"]] = CachedUser(data)

    for user_id in missing:
        if user_id in users:
            continue
        user = None
        if fetch:
            with suppress(discord.HTTPException):
                user = await bot.fetch_user(user_id)
        users[user_id] = user or FakeUser(user_id)

    return users


async def get_or_fetch_member(guild, user_id):
    if member := guild.get_member(user_id):
        return member
    try:
        return await guild.fetch_member(user_id)
    except discord.NotFound:
        return None


async def fetch_role_members(bot, guild, role_ids):
    """Members holding any of the given roles, using the `member` collection when the guild isn't chunked."""

    role_ids = set(role_ids)
    if guild.chunked:
        return [m for m in guild.members if any(r.id in role_ids for r in m.roles)]

    member_ids = await bot.mongo.db.member.distinct(
        "_id.id", {"_id.guild_id": guild.id, "roles": {"$in": list(role_ids)}}
    )
    members = [await get_or_fetch_member(guild, member_id) for member_id in member_ids]
    return [m for m in members if m is not None]


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


DISCORD_DT_FORMAT = "%d %B %Y %H:%M %Z"


def full_format_dt(dt: datetime, plain_text: Optional[bool] = False) -> str:
    """Formats datetime object to discord/text timestamp in `FULL (RELATIVE)` format"""

    if plain_text:
        relative = time.human_timedelta(dt, accuracy=2, brief=True)
        return f"{dt:{DISCORD_DT_FORMAT}} ({relative})"
    else:
        return f"{discord.utils.format_dt(dt)} ({discord.utils.format_dt(dt, 'R')})"


def shorten_around(
    substring: str,
    string: str,
    length: int,
    *,
    placeholder: Optional[str] = "[...]",
    words_before: Optional[int] = 3,
) -> str:
    """
    Function to shorten a string around a given substring. Helpful for things like showing search results.
    """

    words_before += 1
    words = string.split()
    for i, word in enumerate(words):
        if substring in word:
            start = max(0, i - words_before)
            break
    else:
        return shorten(string, length, placeholder=placeholder)

    new_words = []
    if start != 0:
        new_words.append(placeholder)

    new_words.extend(words[start:])

    return shorten(" ".join(new_words), length, placeholder=placeholder)


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


def as_line_chunks_by_len(text: str, char_limit: int):
    """Split a string into chunks by line based on the character limit"""

    chunks = []

    chunk = []
    for line in text.splitlines():
        if len("\n".join(chunk + [line])) > char_limit:
            chunks.append("\n".join(chunk))
            chunk.clear()
        chunk.append(line)

    if chunk:
        chunks.append("\n".join(chunk))

    return chunks
