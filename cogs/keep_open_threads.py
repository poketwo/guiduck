from datetime import datetime, timezone

import discord
from discord.ext import commands
from discord.ext.menus.views import ViewMenuPages

from helpers import checks
from helpers.pagination import EmbedListPageSource

MAX_AUTO_ARCHIVE_MINUTES = 10080

KeepOpenTarget = discord.Thread | discord.TextChannel | discord.ForumChannel


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

    async def is_kept_open(self, thread: discord.Thread) -> bool:
        """Whether the thread is kept open, by its own setting if it has one, or else by its channel's."""

        settings = {
            doc["_id"]: doc["keep_open"]
            async for doc in self.bot.mongo.db.channel.find(
                {"_id": {"$in": [thread.id, thread.parent_id]}, "keep_open": {"$exists": True}}, {"keep_open": 1}
            )
        }
        return settings.get(thread.id, settings.get(thread.parent_id, False))

    async def reopen(self, thread: discord.Thread):
        if thread.archived and not thread.locked:
            await thread.edit(archived=False)

    @commands.Cog.listener()
    async def on_thread_update(self, before: discord.Thread, after: discord.Thread):
        if not before.archived and after.archived and await self.is_kept_open(after):
            await self.reopen(after)

    @commands.Cog.listener()
    async def on_ready(self):
        """Reopens kept threads that were archived while the bot was offline."""

        async for doc in self.bot.mongo.db.channel.find({"keep_open": True}, {"_id": 1}):
            try:
                thread = await self.bot.fetch_channel(doc["_id"])
            except discord.NotFound:
                await self.bot.mongo.db.channel.delete_one({"_id": doc["_id"]})
                continue
            except discord.Forbidden:
                continue
            if not isinstance(thread, discord.Thread):
                continue
            try:
                await self.reopen(thread)
            except discord.HTTPException:
                continue

    @commands.hybrid_group(fallback="on", invoke_without_command=True)
    @checks.is_trial_moderator()
    @commands.guild_only()
    async def keepopen(self, ctx, channel: KeepOpenTarget = commands.CurrentChannel):
        """Keeps a thread, or every thread in a channel, from being archived after inactivity.

        You must have the Trial Moderator role or higher to use this."""

        if not isinstance(channel, KeepOpenTarget):
            return await ctx.send("That's not a thread or a channel with threads.")

        await self.set_keep_open(channel, True)
        if isinstance(channel, discord.Thread):
            await channel.edit(archived=False, auto_archive_duration=MAX_AUTO_ARCHIVE_MINUTES)
            await ctx.send(f"**{channel}** will be kept open.")
        else:
            await ctx.send(f"Threads in **{channel}** will be kept open.")

    @keepopen.command(name="off")
    @checks.is_trial_moderator()
    @commands.guild_only()
    async def keepopen_off(self, ctx, channel: KeepOpenTarget = commands.CurrentChannel):
        """Lets a thread, or the threads in a channel, be archived after inactivity again.

        You must have the Trial Moderator role or higher to use this."""

        if not isinstance(channel, KeepOpenTarget):
            return await ctx.send("That's not a thread or a channel with threads.")

        await self.set_keep_open(channel, False)
        if isinstance(channel, discord.Thread):
            await ctx.send(f"**{channel}** will no longer be kept open.")
        else:
            await ctx.send(f"Threads in **{channel}** will no longer be kept open.")

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
