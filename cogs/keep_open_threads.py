from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands
from discord.ext.menus.views import ViewMenuPages

from helpers import checks
from helpers.pagination import EmbedListPageSource

MAX_AUTO_ARCHIVE_MINUTES = 10080

KeepOpenTarget = discord.Thread | discord.TextChannel | discord.ForumChannel


def archived_from_inactivity(thread: discord.Thread, active_since: datetime | None = None) -> bool:
    """Whether the thread archived by itself after going quiet for its whole timer, rather than someone archiving it.

    *active_since* is a time it was last active besides its last message, like when it was last unarchived."""

    last_activity = discord.utils.snowflake_time(thread.last_message_id or thread.id)
    if active_since is not None:
        last_activity = max(last_activity, active_since)
    return thread.archive_timestamp - last_activity >= timedelta(minutes=thread.auto_archive_duration)


class KeepOpenThreads(commands.Cog):
    """For keeping threads from being archived after inactivity."""

    def __init__(self, bot):
        self.bot = bot

    async def set_keep_open(self, channel: KeepOpenTarget, keep_open: bool):
        update = {"keep_open": keep_open, "guild_id": channel.guild.id}
        if isinstance(channel, discord.Thread):
            update["parent_id"] = channel.parent_id
        elif keep_open:
            update["keep_open_since"] = datetime.now(timezone.utc)
        await self.bot.mongo.db.channel.update_one({"_id": channel.id}, {"$set": update}, upsert=True)

    async def is_kept_open(self, channel: KeepOpenTarget) -> bool:
        """Whether the thread or channel is kept open. A thread goes by its own setting if it has one, or else by its channel's."""

        ids = [channel.id]
        if isinstance(channel, discord.Thread):
            ids.append(channel.parent_id)
        settings = {
            doc["_id"]: doc["keep_open"]
            async for doc in self.bot.mongo.db.channel.find(
                {"_id": {"$in": ids}, "keep_open": {"$exists": True}}, {"keep_open": 1}
            )
        }
        return next((settings[i] for i in ids if i in settings), False)

    async def reopen(self, thread: discord.Thread, active_since: datetime | None = None):
        if thread.archived and not thread.locked and archived_from_inactivity(thread, active_since):
            await thread.edit(archived=False)

    @commands.Cog.listener()
    async def on_thread_update(self, before: discord.Thread, after: discord.Thread):
        if not before.archived and after.archived and await self.is_kept_open(after):
            await self.reopen(after, before.archive_timestamp)

    @commands.Cog.listener()
    async def on_ready(self):
        """Reopens kept threads that were archived while the bot was offline."""

        async for doc in self.bot.mongo.db.channel.find({"keep_open": True}, {"_id": 1, "keep_open_since": 1}):
            try:
                channel = await self.bot.fetch_channel(doc["_id"])
            except discord.NotFound:
                await self.bot.mongo.db.channel.delete_one({"_id": doc["_id"]})
                continue
            except discord.Forbidden:
                continue
            if isinstance(channel, discord.Thread):
                await self.try_reopen(channel)
            elif isinstance(channel, KeepOpenTarget) and "keep_open_since" in doc:
                await self.reopen_archived_since(channel, doc["keep_open_since"])

    async def try_reopen(self, thread: discord.Thread):
        try:
            await self.reopen(thread)
        except discord.HTTPException:
            pass

    async def reopen_archived_since(self, channel: discord.TextChannel | discord.ForumChannel, since: datetime):
        """Reopens the channel's kept threads archived after *since*, going newest-archived first."""

        listings = [channel.archived_threads(limit=None)]
        if isinstance(channel, discord.TextChannel):
            listings.append(channel.archived_threads(limit=None, private=True))

        for listing in listings:
            try:
                async for thread in listing:
                    if thread.archive_timestamp < since:
                        break
                    if await self.is_kept_open(thread):
                        await self.try_reopen(thread)
            except discord.Forbidden:
                continue

    @commands.hybrid_group(fallback="toggle", invoke_without_command=True)
    @checks.is_trial_moderator()
    @commands.guild_only()
    async def keepopen(self, ctx, channel: KeepOpenTarget = commands.CurrentChannel):
        """Toggles keeping a thread, or every thread in a channel, from being archived after inactivity.

        You must have the Trial Moderator role or higher to use this."""

        if not isinstance(channel, KeepOpenTarget):
            return await ctx.send("That's not a thread or a channel with threads.")

        keep_open = not await self.is_kept_open(channel)
        await self.set_keep_open(channel, keep_open)
        if keep_open and isinstance(channel, discord.Thread):
            await channel.edit(archived=False, auto_archive_duration=MAX_AUTO_ARCHIVE_MINUTES)

        name = f"**{channel}**" if isinstance(channel, discord.Thread) else f"Threads in **{channel}**"
        await ctx.send(f"{name} will be kept open." if keep_open else f"{name} will no longer be kept open.")

    @keepopen.command(name="list")
    @checks.is_trial_moderator()
    @commands.guild_only()
    async def keepopen_list(self, ctx):
        """Lists the threads and channels being kept open.

        You must have the Trial Moderator role or higher to use this."""

        docs = await self.bot.mongo.db.channel.find(
            {"guild_id": ctx.guild.id, "keep_open": True}, {"_id": 1, "parent_id": 1}
        ).to_list(None)
        if not docs:
            return await ctx.send("No threads are being kept open.")

        pages = ViewMenuPages(
            source=EmbedListPageSource(
                [f"<#{doc['_id']}>" if "parent_id" in doc else f"<#{doc['_id']}> (all threads)" for doc in docs],
                title=f"Kept Open ({len(docs)})",
            )
        )
        await pages.start(ctx)


async def setup(bot):
    await bot.add_cog(KeepOpenThreads(bot))
