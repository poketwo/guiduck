import json
from datetime import datetime, timedelta
import discord

from discord.ext import commands, menus
from helpers.pagination import EmbedListPageSource


class Automod(commands.Cog):
    """For moderation."""

    def __init__(self, bot):
        self.bot = bot

    async def fetch_banned_words(self, guild):
        words = await self.bot.redis.get(f"banned_words:{guild.id}")
        if words is None:
            data = await self.bot.mongo.db.guild.find_one({"_id": guild.id})
            words = [] if data is None else data.get("banned_words")
            await self.bot.redis.set(f"banned_words:{guild.id}", json.dumps(words), expire=3600)
        else:
            words = json.loads(words)
        return words

    async def update_banned_words(self, guild, push=None, pull=None):
        update = {}
        if push is not None:
            update["$push"] = {"banned_words": {"$each": push}}
        if pull is not None:
            update["$pull"] = {"banned_words": {"$in": pull}}
        await self.bot.mongo.db.guild.update_one({"_id": guild.id}, update, upsert=True)
        await self.bot.redis.delete(f"banned_words:{guild.id}")

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.guild is None:
            return

        words = message.content.casefold().split()
        banned = set(await self.fetch_banned_words(message.guild))

        word = discord.utils.find(lambda x: x in banned, words)
        if word is not None and not message.author.permissions_in(message.channel).administrator:
            ctx = await self.bot.get_context(message)
            await self.automod_punish(ctx, word)

    async def automod_punish(self, ctx, word):
        await ctx.message.delete()
        cog = self.bot.get_cog("Moderation")
        if cog is None:
            return

        query = {
            "target_id": ctx.author.id,
            "user_id": self.bot.user.id,
            "created_at": {"$gt": datetime.utcnow() - timedelta(hours=1)},
        }
        count = await self.bot.mongo.db.action.count_documents(query) + 1

        kwargs = {
            "target": ctx.author,
            "user": self.bot.user,
            "reason": f"Automod: The word `{word}` is banned, watch your language.",
            "created_at": datetime.utcnow(),
        }

        if count >= 10:
            action_cls = cog.cls_dict["mute"]
            kwargs["expires_at"] = kwargs["created_at"] + timedelta(days=1)
        else:
            action_cls = cog.cls_dict["warn"]

        action = action_cls(**kwargs)
        await action.notify()
        await action.execute(ctx)

    @commands.group(invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def automod(self, ctx):
        """Utilities for automoderation."""

        await ctx.send_help(ctx.command)

    @automod.group(invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def words(self, ctx):
        """Displays the banned words list.

        You must have the Administrator permission to use this.
        """

        pages = menus.MenuPages(
            source=EmbedListPageSource(
                await self.fetch_banned_words(ctx.guild),
                title="Banned Words",
                show_index=True,
            )
        )
        await pages.start(ctx)

    @words.command()
    @commands.has_permissions(administrator=True)
    async def add(self, ctx, *words):
        """Adds words to the banned words list.

        You must have the Administrator permission to use this.
        """

        await self.update_banned_words(ctx.guild, push=[x.casefold() for x in words])
        words_msg = ", ".join(f"**{x}**" for x in words)
        await ctx.send(f"Added {words_msg} to the banned words list.")

    @words.command()
    @commands.has_permissions(administrator=True)
    async def remove(self, ctx, *words):
        """Removes words from the banned words list.

        You must have the Administrator permission to use this.
        """

        await self.update_banned_words(ctx.guild, pull=[x.casefold() for x in words])
        words_msg = ", ".join(f"**{x}**" for x in words)
        await ctx.send(f"Removed {words_msg} from the banned words list.")


def setup(bot):
    bot.add_cog(Automod(bot))