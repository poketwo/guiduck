import asyncio
import textwrap
from dataclasses import dataclass
from datetime import datetime
from typing import NamedTuple

import discord
from discord.ext import commands
from discord.ext.menus.views import ViewMenuPages
from helpers import time
from helpers.pagination import AsyncEmbedFieldsPageSource
from helpers.utils import FakeUser


@dataclass
class Reminder:
    user: discord.Member
    event: str
    guild_id: int
    channel_id: int
    message_id: int
    created_at: datetime
    expires_at: datetime
    resolved: bool = False
    _id: int = None

    @classmethod
    def build_from_mongo(cls, bot, x):
        guild = bot.get_guild(x["guild_id"])
        user = guild.get_member(x["user_id"]) or FakeUser(x["user_id"])
        return cls(
            _id=x["_id"],
            user=user,
            event=x["event"],
            guild_id=x["guild_id"],
            channel_id=x["channel_id"],
            message_id=x["message_id"],
            created_at=x["created_at"],
            expires_at=x["expires_at"],
        )

    def to_dict(self):
        return {
            "user_id": self.user.id,
            "event": self.event,
            "guild_id": self.guild_id,
            "channel_id": self.channel_id,
            "message_id": self.message_id,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "resolved": self.resolved,
        }

    def __eq__(self, other):
        if self._id == other._id:
            return

    @property
    def duration(self):
        return self.expires_at - self.created_at


class DispatchedReminder(NamedTuple):
    reminder: Reminder
    task: asyncio.Task


class Reminders(commands.Cog):
    """Reminders to remind you of things."""

    def __init__(self, bot):
        self.bot = bot
        self._current = None
        self.bot.loop.create_task(self.update_current())

    @commands.group(
        invoke_without_command=True, aliases=("remindme", "reminder"), usage="<when> [event]"
    )
    @commands.guild_only()
    async def remind(
        self, ctx, *, when: time.UserFriendlyTime(commands.clean_content, default="\u2026")
    ):
        """Sets a reminder for a date or duration of time, e.g.:

        • in two hours catch some pokemon
        • next thursday do something
        • tomorrow unmute someone

        Times are parsed as UTC.
        """

        reminder = Reminder(
            user=ctx.author,
            event=when.arg,
            guild_id=ctx.guild.id,
            channel_id=ctx.channel.id,
            message_id=ctx.message.id,
            created_at=ctx.message.created_at,
            expires_at=when.dt,
        )

        id = await self.bot.mongo.reserve_id("reminder")
        await self.bot.mongo.db.reminder.insert_one({"_id": id, **reminder.to_dict()})

        reminder._id = id
        self.bot.loop.create_task(self.update_current(reminder))

        await ctx.send(
            f"Alright, I'll remind you in **{time.human_timedelta(reminder.duration)}**: {when.arg}"
        )

    @remind.command()
    @commands.guild_only()
    async def list(self, ctx):
        query = {"resolved": False, "user_id": ctx.author.id}
        count = await self.bot.mongo.db.reminder.count_documents(query)

        async def get_reminders():
            async for x in self.bot.mongo.db.reminder.find(query).sort("expires_at", 1):
                yield Reminder.build_from_mongo(self.bot, x)

        def format_item(i, x):
            name = f"{x._id}. {discord.utils.format_dt(x.expires_at, 'R')}"
            return {"name": name, "value": textwrap.shorten(x.event, 512), "inline": False}

        pages = ViewMenuPages(
            source=AsyncEmbedFieldsPageSource(
                get_reminders(),
                title="Reminders",
                format_item=format_item,
                count=count,
            )
        )

        try:
            await pages.start(ctx)
        except IndexError:
            await ctx.send("No reminders found.")

    @remind.command(aliases=("del",))
    @commands.guild_only()
    async def delete(self, ctx, ids: commands.Greedy[int]):
        """Deletes one or more reminders."""

        result = await self.bot.mongo.db.reminder.delete_many(
            {
                "_id": {"$in": ids},
                "guild_id": ctx.guild.id,
                "resolved": False,
                "user_id": ctx.author.id,
            }
        )
        word = "entry" if result.deleted_count == 1 else "entries"
        await ctx.send(f"Successfully deleted {result.deleted_count} {word}.")
        self.clear_current()
        self.bot.loop.create_task(self.update_current())

    async def get_next_reminder(self):
        return Reminder.build_from_mongo(
            self.bot,
            await self.bot.mongo.db.reminder.find_one(
                {"resolved": False}, sort=(("expires_at", 1),)
            ),
        )

    def clear_current(self):
        self._current.task.cancel()
        self._current = None

    async def update_current(self, reminder=None):
        if reminder is None:
            reminder = await self.get_next_reminder()
            if reminder is None:
                return

        if self._current is not None and not self._current.task.done():
            if reminder.expires_at > self._current.reminder.expires_at:
                return
            self.clear_current()

        self._current = DispatchedReminder(
            reminder=reminder,
            task=self.bot.loop.create_task(self.dispatch_reminder(reminder)),
        )

    async def dispatch_reminder(self, reminder):
        try:
            await discord.utils.sleep_until(reminder.expires_at)
        except asyncio.CancelledError:
            return

        await self.bot.mongo.db.reminder.update_one(
            {"_id": reminder._id}, {"$set": {"resolved": True}}
        )
        guild = self.bot.get_guild(reminder.guild_id)
        channel = guild.get_channel(reminder.channel_id)
        text = (
            f"Reminder from {discord.utils.format_dt(reminder.created_at, 'R')}: {reminder.event}"
        )

        try:
            message = await channel.fetch_message(reminder.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            text = f"{reminder.user.mention} {text}"
            message = None

        await channel.send(
            f"Reminder from {discord.utils.format_dt(reminder.created_at, 'R')}: {reminder.event}",
            reference=message,
        )

        self.bot.loop.create_task(self.update_current())


def setup(bot):
    bot.add_cog(Reminders(bot))
