from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks
from discord.ext.menus.views import ViewMenuPages

from helpers import checks
from helpers.pagination import EmbedListPageSource
from helpers.utils import start_resilient_loop

MAX_AUTO_ARCHIVE_MINUTES = 10080
BUMP_MARGIN = timedelta(hours=1)
REOPENED_TEXT_PREFIX = "-# Reopened this thread since"
BUMPED_TEXT_PREFIX = "-# Bumped this thread since"
KEPT_OPEN_NOTICE_PREFIXES = (REOPENED_TEXT_PREFIX, BUMPED_TEXT_PREFIX)
THREAD_KEPT_OPEN_REASON = "it's marked to be kept open. Use `?keepopen` here to stop."
CHANNEL_KEPT_OPEN_REASON = (
    "threads in <#{channel_id}> are marked to be kept open. Use `?keepopen` here to stop for just this thread."
)

KeepOpenTarget = discord.Thread | discord.TextChannel | discord.ForumChannel


def archived_from_inactivity(thread: discord.Thread, active_since: datetime | None = None) -> bool:
    """Whether the thread archived by itself after going quiet for its whole timer, rather than someone archiving it.

    *active_since* is a time it was last active besides its last message, like when it was last unarchived."""

    last_activity = discord.utils.snowflake_time(thread.last_message_id or thread.id)
    if active_since is not None:
        last_activity = max(last_activity, active_since)
    return thread.archive_timestamp - last_activity >= timedelta(minutes=thread.auto_archive_duration)


def goes_quiet_by(thread: discord.Thread, deadline: datetime) -> bool:
    """Whether the thread's last message will be older than its timer by *deadline*. The channel list hides such
    threads even when Discord hasn't archived them."""

    last_message_at = discord.utils.snowflake_time(thread.last_message_id or thread.id)
    return last_message_at + timedelta(minutes=thread.auto_archive_duration) <= deadline


class KeepOpenThreads(commands.Cog):
    """For keeping threads from being archived after inactivity."""

    def __init__(self, bot):
        self.bot = bot
        start_resilient_loop(self.bump_quiet_threads)

    async def cog_unload(self):
        self.bump_quiet_threads.cancel()

    async def set_keep_open(self, channel: KeepOpenTarget, keep_open: bool):
        update = {"keep_open": keep_open, "guild_id": channel.guild.id}
        if isinstance(channel, discord.Thread):
            update["parent_id"] = channel.parent_id
        elif keep_open:
            update["keep_open_since"] = datetime.now(timezone.utc)
        await self.bot.mongo.db.channel.update_one({"_id": channel.id}, {"$set": update}, upsert=True)

    async def kept_open_by(self, channel: KeepOpenTarget) -> int | None:
        """The ID of the thread or channel whose setting keeps it open, if it's kept open. A thread goes by its own
        setting if it has one, or else by its channel's."""

        ids = [channel.id]
        if isinstance(channel, discord.Thread):
            ids.append(channel.parent_id)
        return self.resolve_kept_open_by(ids, await self.keep_open_settings(ids))

    async def keep_open_settings(self, ids: list[int]) -> dict[int, bool]:
        """The keep-open settings of whichever of the threads and channels have one."""

        return {
            doc["_id"]: doc["keep_open"]
            async for doc in self.bot.mongo.db.channel.find(
                {"_id": {"$in": ids}, "keep_open": {"$exists": True}}, {"keep_open": 1}
            )
        }

    @staticmethod
    def resolve_kept_open_by(ids: list[int], settings: dict[int, bool]) -> int | None:
        """The first of *ids* that has a setting, if that setting keeps it open."""

        setting_id = next((i for i in ids if i in settings), None)
        return setting_id if setting_id is not None and settings[setting_id] else None

    async def is_kept_open(self, channel: KeepOpenTarget) -> bool:
        return await self.kept_open_by(channel) is not None

    async def reopen(self, thread: discord.Thread, active_since: datetime | None = None):
        """Reopens the thread if it's kept open and archived from inactivity, with a message saying why. The message
        also brings it back to the channel list, which hides threads whose last message is older than their timer."""

        if not thread.archived or thread.locked or not archived_from_inactivity(thread, active_since):
            return
        kept_open_by = await self.kept_open_by(thread)
        if kept_open_by is None:
            return

        await thread.edit(archived=False)
        await self.send_kept_open_notice(thread, REOPENED_TEXT_PREFIX, kept_open_by)

    async def send_kept_open_notice(self, thread: discord.Thread, prefix: str, kept_open_by: int):
        """Replaces the thread's last kept-open notice with a new one saying what keeps it open."""

        await self.delete_last_kept_open_notice(thread)
        if kept_open_by == thread.id:
            reason = THREAD_KEPT_OPEN_REASON
        else:
            reason = CHANNEL_KEPT_OPEN_REASON.format(channel_id=kept_open_by)
        await thread.send(f"{prefix} {reason}", silent=True)

    async def delete_last_kept_open_notice(self, thread: discord.Thread):
        """Deletes the thread's last message if it's an earlier kept-open notice, so a quiet thread doesn't pile them
        up."""

        if thread.last_message_id is None:
            return
        try:
            message = await thread.fetch_message(thread.last_message_id)
        except (discord.NotFound, discord.Forbidden):
            return
        if message.author == self.bot.user and message.content.startswith(KEPT_OPEN_NOTICE_PREFIXES):
            await message.delete()

    @commands.Cog.listener()
    async def on_thread_update(self, before: discord.Thread, after: discord.Thread):
        if not before.archived and after.archived:
            await self.reopen(after, before.archive_timestamp)

    @tasks.loop(minutes=30)
    async def bump_quiet_threads(self):
        """Posts in kept threads about to go quiet for their whole timer. Discord often never archives these, so
        there's nothing to reopen, but the channel list hides them all the same."""

        deadline = datetime.now(timezone.utc) + BUMP_MARGIN
        threads = [
            thread
            for guild in self.bot.guilds
            for thread in guild.threads
            if not thread.archived and not thread.locked and goes_quiet_by(thread, deadline)
        ]
        if not threads:
            return

        settings = await self.keep_open_settings(list({i for t in threads for i in (t.id, t.parent_id)}))
        for thread in threads:
            kept_open_by = self.resolve_kept_open_by([thread.id, thread.parent_id], settings)
            if kept_open_by is None:
                continue
            try:
                if thread.auto_archive_duration < MAX_AUTO_ARCHIVE_MINUTES:
                    await thread.edit(auto_archive_duration=MAX_AUTO_ARCHIVE_MINUTES)
                await self.send_kept_open_notice(thread, BUMPED_TEXT_PREFIX, kept_open_by)
            except discord.HTTPException:
                continue

    @bump_quiet_threads.before_loop
    async def before_bump_quiet_threads(self):
        await self.bot.wait_until_ready()

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
