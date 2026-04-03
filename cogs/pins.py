import asyncio
import textwrap
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Union

import discord
from discord.ext import commands
from discord.ext.menus.views import ViewMenuPages
from discord.utils import format_dt

from helpers import time
from helpers.pagination import AsyncEmbedFieldsPageSource

# Discord's PIN_MESSAGES permission (1 << 51), not yet in this discord.py fork
PIN_MESSAGES_BIT = 1 << 51

# Discord increased the pin limit from 50 to 250 in August 2025
MAX_PINS = 250


@dataclass
class TimedPin:
    user_id: int
    guild_id: int
    channel_id: int
    message_id: int
    created_at: datetime
    expires_at: datetime
    resolved: bool = False
    _id: int = None

    @classmethod
    def build_from_mongo(cls, x):
        return cls(
            _id=x["_id"],
            user_id=x["user_id"],
            guild_id=x["guild_id"],
            channel_id=x["channel_id"],
            message_id=x["message_id"],
            created_at=x["created_at"],
            expires_at=x["expires_at"],
            resolved=x.get("resolved", False),
        )

    def to_dict(self):
        return {
            "user_id": self.user_id,
            "guild_id": self.guild_id,
            "channel_id": self.channel_id,
            "message_id": self.message_id,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "resolved": self.resolved,
        }

    @property
    def duration(self):
        return self.expires_at - self.created_at


def can_pin():
    """Check that the user has pin permission or is the thread owner."""

    async def predicate(ctx):
        perms = ctx.channel.permissions_for(ctx.author)

        # Users with manage_messages can always pin
        if perms.manage_messages:
            return True

        # Check PIN_MESSAGES (1 << 51) via raw value since our discord.py fork doesn't have it yet
        if perms.value & PIN_MESSAGES_BIT:
            return True

        # Thread owner can pin in their own thread
        if isinstance(ctx.channel, discord.Thread) and ctx.channel.owner_id == ctx.author.id:
            return True

        raise commands.CheckFailure("You must have the Pin Messages permission to use this.")

    return commands.check(predicate)


class PinIndexOrMessage(commands.Converter):
    """Accepts either a pin index (e.g. #1, #2) or a message link/ID."""

    async def convert(self, ctx, argument: str) -> Union[int, discord.Message]:
        # Check for pin index (e.g. #1 or just a small number)
        stripped = argument.lstrip("#")
        if stripped.isdigit():
            idx = int(stripped)
            if 1 <= idx <= MAX_PINS:
                return idx

        return await commands.MessageConverter().convert(ctx, argument)


class Pins(commands.Cog):
    """For pinning and unpinning messages."""

    def __init__(self, bot):
        self.bot = bot
        self._current = None
        self.bot.loop.create_task(self.update_current())

    async def resolve_message(self, ctx, message: Optional[str] = None) -> discord.Message:
        """Resolve a message from a reply, link, or ID."""

        if message is not None:
            return await commands.MessageConverter().convert(ctx, message)

        if ctx.message.reference is not None and ctx.message.reference.message_id is not None:
            try:
                return await ctx.channel.fetch_message(ctx.message.reference.message_id)
            except discord.NotFound:
                raise commands.BadArgument("The replied-to message was not found.")

        raise commands.BadArgument("Please reply to a message or provide a message link/ID.")

    async def parse_message_and_duration(self, ctx, args: Optional[str] = None):
        """Parse the combined message + duration arguments.

        Supports:
        - ?pin (reply, no duration)
        - ?pin <message> (no duration)
        - ?pin <duration> (reply, with duration)
        - ?pin <message> <duration>
        """

        if args is None:
            return await self.resolve_message(ctx), None

        parts = args.split(None, 1)
        first = parts[0]

        # Try to resolve the first part as a message
        msg = None
        try:
            msg = await commands.MessageConverter().convert(ctx, first)
        except commands.MessageNotFound:
            pass

        if msg is not None:
            # First part is a message, rest (if any) is duration
            duration_str = parts[1] if len(parts) > 1 else None
            if duration_str is not None:
                try:
                    duration = time.FutureTime(duration_str, now=ctx.message.created_at)
                except commands.BadArgument:
                    raise commands.BadArgument(f"Invalid duration: `{duration_str}`")
                return msg, duration
            return msg, None

        # First part is not a message, try the whole thing as a duration (user is replying)
        try:
            duration = time.FutureTime(args, now=ctx.message.created_at)
        except commands.BadArgument:
            raise commands.BadArgument(
                f"Could not find a message matching `{first}`. Please reply to a message or provide a valid message link/ID."
            )

        return await self.resolve_message(ctx), duration

    @commands.group(invoke_without_command=True)
    @commands.guild_only()
    @can_pin()
    async def pin(self, ctx, *, args: Optional[str] = None):
        """Toggles a pin on a message.

        If the message is not pinned, it will be pinned. If it is already pinned, it will be unpinned.

        You can reply to the message, or pass a message link/ID.

        Optionally provide a duration to pin temporarily, e.g.:
        \u2022 ?pin <message> 2h
        \u2022 ?pin 30m (when replying)
        """

        target, duration = await self.parse_message_and_duration(ctx, args)

        if target.pinned:
            # Unpin the message (toggle off)
            await target.unpin(reason=f"Unpinned by {ctx.author} (ID: {ctx.author.id})")

            try:
                await target.remove_reaction("\N{PUSHPIN}", self.bot.user)
            except (discord.Forbidden, discord.HTTPException):
                pass

            # Resolve any timed pin for this message
            await self.bot.mongo.db.timed_pin.update_many(
                {"message_id": target.id, "channel_id": ctx.channel.id, "resolved": False},
                {"$set": {"resolved": True}},
            )

            if self._current is not None:
                self.clear_current()
            self.bot.loop.create_task(self.update_current())

            return await ctx.send("\N{PUSHPIN} Unpinned!")

        # Pin the message (toggle on)
        await target.pin(reason=f"Pinned by {ctx.author} (ID: {ctx.author.id})")

        try:
            await target.add_reaction("\N{PUSHPIN}")
        except (discord.Forbidden, discord.HTTPException):
            pass

        if duration is not None:
            timed_pin = TimedPin(
                user_id=ctx.author.id,
                guild_id=ctx.guild.id,
                channel_id=ctx.channel.id,
                message_id=target.id,
                created_at=ctx.message.created_at,
                expires_at=duration.dt,
            )

            id = await self.bot.mongo.reserve_id("timed_pin")
            await self.bot.mongo.db.timed_pin.insert_one({"_id": id, **timed_pin.to_dict()})

            timed_pin._id = id
            self.bot.loop.create_task(self.update_current(timed_pin))

            await ctx.send(
                f"\N{PUSHPIN} Pinned! This message will be automatically unpinned {format_dt(duration.dt, 'R')}."
            )
        else:
            await ctx.send("\N{PUSHPIN} Pinned!")

    @pin.command(name="list", aliases=("ls",))
    @commands.guild_only()
    async def pin_list(self, ctx):
        """Lists all pinned messages in the current channel.

        Use ?pin view <#index> to view a specific pin's full content.
        """

        pinned = await ctx.channel.pins()

        if not pinned:
            return await ctx.send("There are no pinned messages in this channel.")

        count = len(pinned)

        async def get_pins():
            for msg in pinned:
                yield msg

        def format_item(i, msg):
            content = msg.content or "*No text content*"
            preview = textwrap.shorten(content, 100)
            jump = f"{msg.jump_url}"
            name = f"#{i + 1}. {msg.author.display_name} \u2022 {jump}"
            value = f"{preview}\n{format_dt(msg.created_at, 'R')}"
            return {"name": name, "value": value, "inline": False}

        pages = ViewMenuPages(
            source=AsyncEmbedFieldsPageSource(
                get_pins(),
                title=f"\N{PUSHPIN} Pinned Messages ({count}/{MAX_PINS})",
                format_item=format_item,
                count=count,
            )
        )

        try:
            await pages.start(ctx)
        except IndexError:
            await ctx.send("No pinned messages found.")

    @pin.command(name="view", aliases=("show", "info"))
    @commands.guild_only()
    async def pin_view(self, ctx, *, target: PinIndexOrMessage):
        """Shows a specific pinned message's content in an embed.

        You can pass a message link/ID, or use an index from ?pin list (e.g. #1).
        """

        if isinstance(target, int):
            # Resolve index to a pinned message
            pinned = await ctx.channel.pins()
            if not pinned:
                return await ctx.send("There are no pinned messages in this channel.")
            if target < 1 or target > len(pinned):
                return await ctx.send(
                    f"Invalid pin index. There are **{len(pinned)}** pinned messages (use #1-#{len(pinned)})."
                )
            message = pinned[target - 1]
        else:
            message = target
            if not message.pinned:
                return await ctx.send("That message is not pinned.")

        embed = discord.Embed(
            description=message.content or "*No text content*",
            color=discord.Color.blurple(),
            timestamp=message.created_at,
        )
        embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
        embed.add_field(name="Jump", value=f"[Go to message]({message.jump_url})", inline=True)

        if message.attachments:
            attachment_text = "\n".join(f"[{a.filename}]({a.url})" for a in message.attachments)
            embed.add_field(name="Attachments", value=attachment_text, inline=False)
            # Set the first image attachment as the embed image
            for attachment in message.attachments:
                if attachment.content_type and attachment.content_type.startswith("image/"):
                    embed.set_image(url=attachment.url)
                    break

        embed.set_footer(text=f"Pin #{target}" if isinstance(target, int) else "Pinned message")

        await ctx.send(embed=embed)

    # Timed pin dispatch system (modeled after reminders)

    async def get_next_timed_pin(self):
        timed_pin = await self.bot.mongo.db.timed_pin.find_one({"resolved": False}, sort=(("expires_at", 1),))
        if timed_pin is None:
            return None
        return TimedPin.build_from_mongo(timed_pin)

    def clear_current(self):
        self._current.task.cancel()
        self._current = None

    async def update_current(self, timed_pin=None):
        await self.bot.wait_until_ready()

        if timed_pin is None:
            timed_pin = await self.get_next_timed_pin()
            if timed_pin is None:
                return

        if self._current is not None and not self._current.task.done():
            if timed_pin.expires_at > self._current.timed_pin.expires_at:
                return
            self.clear_current()

        self._current = DispatchedTimedPin(
            timed_pin=timed_pin,
            task=self.bot.loop.create_task(self.dispatch_timed_pin(timed_pin)),
        )

    async def dispatch_timed_pin(self, timed_pin):
        try:
            await discord.utils.sleep_until(timed_pin.expires_at)
        except asyncio.CancelledError:
            return

        await self.bot.mongo.db.timed_pin.update_one({"_id": timed_pin._id}, {"$set": {"resolved": True}})

        guild = self.bot.get_guild(timed_pin.guild_id)
        if guild is None:
            self.bot.loop.create_task(self.update_current())
            return

        channel = guild.get_channel_or_thread(timed_pin.channel_id)
        if channel is None:
            self.bot.loop.create_task(self.update_current())
            return

        try:
            message = await channel.fetch_message(timed_pin.message_id)
            if message.pinned:
                await message.unpin(reason="Timed pin expired")
                try:
                    await message.remove_reaction("\N{PUSHPIN}", self.bot.user)
                except (discord.Forbidden, discord.HTTPException):
                    pass
                await channel.send(
                    f"\N{PUSHPIN} A temporarily pinned message has been automatically unpinned.",
                    reference=message,
                )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

        self.bot.loop.create_task(self.update_current())


class DispatchedTimedPin:
    __slots__ = ("timed_pin", "task")

    def __init__(self, timed_pin, task):
        self.timed_pin = timed_pin
        self.task = task


async def setup(bot):
    await bot.add_cog(Pins(bot))
