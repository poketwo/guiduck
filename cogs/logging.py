import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks
from pymongo import UpdateOne

from helpers import checks


class Logging(commands.Cog):
    """For logging."""

    def __init__(self, bot):
        self.bot = bot
        self.log = logging.getLogger("support")
        self.cache_all.start()

    @tasks.loop(minutes=20)
    async def cache_all(self):
        for guild in self.bot.guilds:
            await self.full_cache_guild(guild)

    @cache_all.before_loop
    async def before_cache_all(self):
        return await self.bot.wait_until_ready()

    def serialize_role(self, role):
        return {
            "id": role.id,
            "name": role.name,
            "color": role.color.value,
            "position": role.position,
        }

    async def full_cache_guild(self, guild):
        await self.bot.mongo.db.guild.bulk_write([self.make_cache_guild(guild)])
        await self.bot.mongo.db.channel.bulk_write([self.make_cache_channel(channel) for channel in guild.channels])
        await self.bot.mongo.db.member.bulk_write([self.make_cache_member(member) for member in guild.members])

    def make_cache_guild(self, guild):
        return UpdateOne(
            {"_id": guild.id},
            {
                "$set": {
                    "name": guild.name,
                    "icon": guild.icon and str(guild.icon.url),
                    "roles": [self.serialize_role(x) for x in guild.roles],
                }
            },
            upsert=True,
        )

    def make_cache_channel(self, channel):
        base = {
            "guild_id": channel.guild.id,
            "type": str(channel.type),
            "name": channel.name,
            "position": channel.position,
        }
        if isinstance(channel, (discord.TextChannel, discord.VoiceChannel)):
            base["category_id"] = channel.category_id
        if isinstance(channel, discord.TextChannel):
            base["last_message_id"] = channel.last_message_id

        return UpdateOne({"_id": channel.id}, {"$set": base}, upsert=True)

    def make_cache_member(self, member):
        return UpdateOne(
            {"_id": {"id": member.id, "guild_id": member.guild.id}},
            {
                "$set": {
                    "name": member.name,
                    "discriminator": member.discriminator,
                    "nick": member.nick,
                    "avatar": str(member.display_avatar.url),
                    "roles": [x.id for x in member.roles],
                }
            },
            upsert=True,
        )

    @commands.Cog.listener(name="on_guild_join")
    @commands.Cog.listener(name="on_guild_update")
    async def on_guild_updates(self, *args):
        await self.bot.mongo.db.guild.bulk_write([self.make_cache_guild(args[-1])])

    @commands.Cog.listener(name="on_member_join")
    @commands.Cog.listener(name="on_member_update")
    async def on_member_updates(self, *args):
        thing = args[-1]
        await self.bot.mongo.db.member.bulk_write([self.make_cache_member(thing)])

    @commands.Cog.listener()
    async def on_user_update(self, _, new):
        for guild in self.bot.guilds:
            member = guild.get_member(new.id)
            if member is None:
                continue
            await self.bot.mongo.db.member.bulk_write([self.make_cache_member(member)])

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        for channel in guild.channels:
            await self.bot.mongo.db.channel.bulk_write([self.make_cache_channel(channel)])

    @commands.Cog.listener(name="on_guild_channel_create")
    @commands.Cog.listener(name="on_guild_channel_update")
    async def on_guild_channel_updates(self, *args):
        await self.bot.mongo.db.channel.bulk_write([self.make_cache_channel(args[-1])])

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        await self.bot.mongo.db.channel.delete_one({"_id": channel.id})

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.guild is None:
            return

        time = int(message.created_at.replace(tzinfo=timezone.utc).timestamp() - 3600)
        await self.bot.mongo.db.message.insert_one(
            {
                "_id": message.id,
                "user_id": message.author.id,
                "channel_id": message.channel.id,
                "guild_id": message.guild.id,
                "history": {str(time): message.content},
                "attachments": [
                    {"id": attachment.id, "filename": attachment.filename} for attachment in message.attachments
                ],
                "deleted_at": None,
            }
        )
        await self.bot.mongo.db.channel.update_one(
            {"_id": message.channel.id}, {"$set": {"last_message_id": message.id}}
        )

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload):
        if "content" not in payload.data:
            return
        time = int(datetime.now(timezone.utc).timestamp()) - 3600
        await self.bot.mongo.db.message.update_one(
            {"_id": payload.message_id},
            {"$set": {f"history.{time}": payload.data["content"]}},
        )

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload):
        if payload.cached_message is not None:
            for attachment in payload.cached_message.attachments:
                fn = f"attachments/{attachment.id}_{attachment.filename}"
                self.bot.loop.create_task(attachment.save(fn, use_cached=True))
        await self.bot.mongo.db.message.update_one(
            {"_id": payload.message_id},
            {"$set": {"deleted_at": datetime.now(timezone.utc)}},
        )

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload):
        await self.bot.mongo.db.message.update_many(
            {"_id": {"$in": list(payload.message_ids)}},
            {"$set": {"deleted_at": datetime.now(timezone.utc)}},
        )

    @commands.group(invoke_without_command=True)
    @checks.is_trial_moderator()
    @commands.guild_only()
    async def logs(self, ctx, *, channel: discord.TextChannel = None):
        """Gets a link to the message logs for a channel.

        You must have the Trial Moderator role to use this.
        """

        channel = channel or ctx.channel
        await ctx.send(f"https://admin.poketwo.net/logs/{channel.guild.id}/{channel.id}")

    @logs.command()
    @commands.guild_only()
    @checks.is_community_manager()
    async def restrict(self, ctx, channel: discord.TextChannel = None):
        """Restricts the logs for a channel to Admins.

        You must have the Community Manager role to use this.
        """

        channel = channel or ctx.channel
        await self.bot.mongo.db.channel.update_one({"_id": channel.id}, {"$set": {"restricted": True}})
        await ctx.send(f"Restricted logs for **#{channel}** to Admins.")

    @logs.command(name="sync-cache")
    @commands.guild_only()
    @checks.is_community_manager()
    async def sync_cache(self, ctx):
        """Syncs all caches for the current server.

        You must have the Community Manager role to use this."""

        await ctx.send("Syncing all caches for this guild...")
        await self.full_cache_guild(ctx.guild)
        await ctx.send("Completed cache sync.")

    async def cog_unload(self):
        self.cache_all.cancel()


async def setup(bot):
    await bot.add_cog(Logging(bot))
