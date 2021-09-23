import asyncio
import sys
import traceback

import discord
from discord.ext import commands
from helpers.utils import FakeUser

CHANNEL_ID = 888320631890931732


class Forms(commands.Cog):
    """For posting form submissions."""

    def __init__(self, bot):
        self.bot = bot
        self._task = bot.loop.create_task(self.watch_submissions())

    def cog_unload(self):
        self._task.cancel()

    async def send_submission(self, channel, submission):
        user = self.bot.get_user(submission["user_id"]) or FakeUser(submission["user_id"])
        embed = discord.Embed(
            title=f"New Form Submission ({submission['form_id']})",
            url=f"https://forms.poketwo.net/a/{submission['form_id']}/submissions/{submission['_id']}",
            color=discord.Color.blurple(),
        )
        embed.set_author(name=str(user), icon_url=user.display_avatar.url)
        await channel.send(embed=embed)

    async def _watch_submissions(self, channel):
        pipeline = [{"$match": {"operationType": "insert"}}]
        async with self.bot.mongo.client.support.submission.watch(pipeline) as change_stream:
            async for change in change_stream:
                print(change)
                await self.send_submission(channel, change["fullDocument"])

    async def watch_submissions(self):
        await self.bot.wait_until_ready()
        channel = self.bot.get_channel(CHANNEL_ID)
        while True:
            try:
                await self._watch_submissions(channel)
            except asyncio.CancelledError:
                return
            except Exception as error:
                print("Ignoring exception in watch submissions")
                traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)


def setup(bot):
    bot.add_cog(Forms(bot))
