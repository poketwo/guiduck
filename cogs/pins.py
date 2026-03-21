import asyncio
import textwrap
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import discord
from discord.ext import commands
from discord.ext.menus.views import ViewMenuPages
from discord.utils import format_dt

from helpers import time
from helpers.pagination import AsyncEmbedFieldsPageSource


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


class PinFlags(commands.FlagConverter, prefix="--", delimiter=" "):
    duration: Optional[time.FutureTime] = commands.flag(
        name="duration",
        aliases=("d", "for", "in", "time", "t"),
        description="Duration to keep the message pinned before auto-unpinning",
        max_args=1,
        default=None,
    )


def can_pin():
    """Check that the user is the thread owner or has manage_messages permission."""

    async def predicate(ctx):
        if not isinstance(ctx.channel, discord.Thread):
            raise commands.CheckFailure("This command can only be used in threads.")

        # Users with manage_messages can always pin
        if ctx.channel.permissions_for(ctx.author).manage_messages:
            return True

        # Thread owner can pin in their own thread
        if ctx.channel.owner_id == ctx.author.id:
            return True

        raise commands.CheckFailure("You must be the thread owner or have the Manage Messages permission to use this.")

    return commands.check(predicate)


class Pins(commands.Cog):
    """For pinning and unpinning messages in threads."""

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

    @commands.group(invoke_without_command=True)
    @commands.guild_only()
    @can_pin()
    async def pin(self, ctx, message: Optional[str] = None, *, flags: PinFlags):
        """Pins a message in the current thread.

        You can reply to the message you want to pin, or pass a message link/ID.

        Use --duration to pin temporarily, e.g.:
        • ?pin <message> --duration 2h
        • ?pin --duration 30m (when replying)
        """

        target = await self.resolve_message(ctx, message)

        if target.pinned:
            return await ctx.send("That message is already pinned.")

        await target.pin(reason=f"Pinned by {ctx.author} (ID: {ctx.author.id})")

        if flags.duration is not None:
            timed_pin = TimedPin(
                user_id=ctx.author.id,
                guild_id=ctx.guild.id,
                channel_id=ctx.channel.id,
                message_id=target.id,
                created_at=ctx.message.created_at,
                expires_at=flags.duration.dt,
            )

            id = await self.bot.mongo.reserve_id("timed_pin")
            await self.bot.mongo.db.timed_pin.insert_one({"_id": id, **timed_pin.to_dict()})

            timed_pin._id = id
            self.bot.loop.create_task(self.update_current(timed_pin))

            await ctx.send(
                f"\N{PUSHPIN} Pinned! This message will be automatically unpinned {format_dt(flags.duration.dt, 'R')}."
            )
        else:
            await ctx.send("\N{PUSHPIN} Pinned!")

    @pin.command(name="remove", aliases=("unpin",))
    @commands.guild_only()
    @can_pin()
    async def pin_remove(self, ctx, *, message: Optional[str] = None):
        """Unpins a message in the current thread.

        You can reply to the message you want to unpin, or pass a message link/ID.
        """

        target = await self.resolve_message(ctx, message)

        if not target.pinned:
            return await ctx.send("That message is not pinned.")

        await target.unpin(reason=f"Unpinned by {ctx.author} (ID: {ctx.author.id})")

        # Resolve any timed pin for this message
        await self.bot.mongo.db.timed_pin.update_many(
            {"message_id": target.id, "channel_id": ctx.channel.id, "resolved": False},
            {"$set": {"resolved": True}},
        )

        if self._current is not None:
            self.clear_current()
        self.bot.loop.create_task(self.update_current())

        await ctx.send("\N{PUSHPIN} Unpinned!")

    @pin.command(name="list", aliases=("ls",))
    @commands.guild_only()
    async def pin_list(self, ctx):
        """Lists all pinned messages in the current channel."""

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
            jump = f"[Jump]({msg.jump_url})"
            name = f"{msg.author.display_name} \u2022 {jump}"
            value = f"{preview}\n{format_dt(msg.created_at, 'R')}"
            return {"name": name, "value": value, "inline": False}

        pages = ViewMenuPages(
            source=AsyncEmbedFieldsPageSource(
                get_pins(),
                title=f"\N{PUSHPIN} Pinned Messages ({count}/50)",
                format_item=format_item,
                count=count,
            )
        )

        try:
            await pages.start(ctx)
        except IndexError:
            await ctx.send("No pinned messages found.")

    @pin.command(name="clear")
    @commands.guild_only()
    @can_pin()
    async def pin_clear(self, ctx):
        """Unpins all messages in the current thread."""

        pinned = await ctx.channel.pins()

        if not pinned:
            return await ctx.send("There are no pinned messages to clear.")

        result = await ctx.confirm(f"Are you sure you want to unpin all **{len(pinned)}** pinned messages?")

        if result is None or result is False:
            return await ctx.send("Cancelled.")

        reason = f"Bulk unpin by {ctx.author} (ID: {ctx.author.id})"
        for msg in pinned:
            await msg.unpin(reason=reason)

        # Resolve all timed pins in this channel
        await self.bot.mongo.db.timed_pin.update_many(
            {"channel_id": ctx.channel.id, "resolved": False},
            {"$set": {"resolved": True}},
        )

        if self._current is not None:
            self.clear_current()
        self.bot.loop.create_task(self.update_current())

        await ctx.send(f"\N{PUSHPIN} Unpinned **{len(pinned)}** messages.")

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
