from collections import defaultdict
from datetime import datetime, timedelta, timezone

from discord.ext import commands, tasks

from helpers import constants


class AutoLockThreads(commands.Cog):
    """For automatically locking threads."""

    def __init__(self, bot):
        self.bot = bot
        self.lock_threads.start()
        self.warned_thread_ids = defaultdict(set)

    async def send_warning(self, thread, time):
        if thread.id in self.warned_thread_ids[time]:
            return
        await thread.send(f"This thread will be locked in **{time}**.")
        self.warned_thread_ids[time].add(thread.id)

    def clear_warnings(self, thread):
        for time in self.warned_thread_ids:
            self.warned_thread_ids[time].discard(thread.id)

    @tasks.loop(seconds=15)
    async def lock_threads(self):
        guild = self.bot.get_guild(constants.COMMUNITY_SERVER_ID)
        channel = guild.get_channel(1019656562119295098)

        for thread in channel.threads:
            if thread.flags.pinned:
                continue
            time_left = thread.created_at + timedelta(days=7) - datetime.now(timezone.utc)

            if time_left < timedelta():
                await thread.edit(archived=True, locked=True)
                self.clear_warnings(thread)
            elif time_left < timedelta(hours=1):
                await self.send_warning(thread, "1 hour")
            elif timedelta(hours=23) < time_left < timedelta(days=1):
                await self.send_warning(thread, "24 hours")

    @lock_threads.before_loop
    async def before_lock_threads(self):
        await self.bot.wait_until_ready()

    async def cog_unload(self):
        self.lock_threads.cancel()


async def setup(bot):
    await bot.add_cog(AutoLockThreads(bot))
