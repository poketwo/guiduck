import discord
from discord.ext import commands

from helpers import checks

MAX_AUTO_ARCHIVE_MINUTES = 10080


class KeepOpenThreads(commands.Cog):
    """For keeping threads from being archived after inactivity."""

    def __init__(self, bot):
        self.bot = bot

    async def set_keep_open(self, thread: discord.Thread, keep_open: bool):
        await self.bot.mongo.db.channel.update_one(
            {"_id": thread.id},
            {"$set": {"keep_open": keep_open, "guild_id": thread.guild.id, "parent_id": thread.parent_id}},
            upsert=True,
        )

    async def is_kept_open(self, thread: discord.Thread) -> bool:
        return await self.bot.mongo.db.channel.count_documents({"_id": thread.id, "keep_open": True}) > 0

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
            await self.reopen(thread)

    @commands.hybrid_group(fallback="on", invoke_without_command=True)
    @checks.is_trial_moderator()
    @commands.guild_only()
    async def keepopen(self, ctx, thread: discord.Thread = commands.CurrentChannel):
        """Keeps a thread from being archived after inactivity.

        You must have the Trial Moderator role or higher to use this."""

        if not isinstance(thread, discord.Thread):
            return await ctx.send("That's not a thread.")

        await self.set_keep_open(thread, True)
        await thread.edit(archived=False, auto_archive_duration=MAX_AUTO_ARCHIVE_MINUTES)
        await ctx.send(f"**{thread}** will be kept open.")

    @keepopen.command(name="off")
    @checks.is_trial_moderator()
    @commands.guild_only()
    async def keepopen_off(self, ctx, thread: discord.Thread = commands.CurrentChannel):
        """Lets a thread be archived after inactivity again.

        You must have the Trial Moderator role or higher to use this."""

        if not isinstance(thread, discord.Thread):
            return await ctx.send("That's not a thread.")

        await self.set_keep_open(thread, False)
        await ctx.send(f"**{thread}** will no longer be kept open.")

    @keepopen.command(name="list")
    @checks.is_trial_moderator()
    @commands.guild_only()
    async def keepopen_list(self, ctx):
        """Lists the threads being kept open.

        You must have the Trial Moderator role or higher to use this."""

        docs = await self.bot.mongo.db.channel.find({"guild_id": ctx.guild.id, "keep_open": True}, {"_id": 1}).to_list(
            None
        )
        if not docs:
            return await ctx.send("No threads are being kept open.")
        await ctx.send("\n".join(f"- <#{doc['_id']}>" for doc in docs))


async def setup(bot):
    await bot.add_cog(KeepOpenThreads(bot))
