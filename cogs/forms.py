import asyncio
import sys
import traceback
from enum import Enum

import discord
from discord.ext import commands
from helpers.utils import FakeUser


class SubmissionStatus(Enum):
    UNDER_REVIEW = 0
    REJECTED = 1
    ACCEPTED = 2
    MARKED = 3


CHANNEL_ID = 888320631890931732

COLORS = {
    SubmissionStatus.UNDER_REVIEW.value: discord.Embed.Empty,
    SubmissionStatus.REJECTED.value: discord.Color.red(),
    SubmissionStatus.ACCEPTED.value: discord.Color.green(),
    SubmissionStatus.MARKED.value: discord.Color.blurple(),
}

TEXT = {
    SubmissionStatus.UNDER_REVIEW.value: "New Form Submission",
    SubmissionStatus.REJECTED.value: "Rejected",
    SubmissionStatus.ACCEPTED.value: "Accepted",
    SubmissionStatus.MARKED.value: "Marked for Review",
}


class Forms(commands.Cog):
    """For posting form submissions."""

    def __init__(self, bot):
        self.bot = bot
        self._task = bot.loop.create_task(self.watch_submissions())

    def cog_unload(self):
        self._task.cancel()

    async def send_submission(self, channel, submission):
        status = submission.get("status", 0)
        user = self.bot.get_user(submission["user_id"]) or FakeUser(submission["user_id"])

        embed = discord.Embed(
            title=f"{TEXT[status]} ({submission['form_id']})",
            url=f"https://forms.poketwo.net/a/{submission['form_id']}/submissions/{submission['_id']}",
            color=COLORS[status],
        )
        embed.set_author(name=submission["user_tag"], icon_url=user.display_avatar.url)
        embed.set_footer(text=f"User ID • {user.id}")

        if embedded_id := submission.get("embedded_id"):
            message = await channel.fetch_message(embedded_id)
            return await message.edit(embed=embed)
        else:
            message = await channel.send(embed=embed)
            await self.bot.mongo.db.submission.update_one(
                {"_id": submission["_id"]}, {"$set": {"embedded_id": message.id}}
            )

    async def _watch_submissions(self, channel):
        coll = self.bot.mongo.db.submission
        pipeline = [{"$match": {"operationType": {"$in": ["insert", "replace", "update"]}}}]
        async for change in coll.watch(pipeline, full_document="updateLookup"):
            await self.send_submission(channel, change["fullDocument"])

    async def watch_submissions(self):
        await self.bot.wait_until_ready()
        channel = self.bot.get_channel_or_thread(CHANNEL_ID)
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
