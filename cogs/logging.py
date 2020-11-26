import logging
from datetime import datetime

import discord
from discord.ext import commands

formatter = logging.Formatter("%(asctime)s:%(levelname)s:%(name)s: %(message)s")

GUILD_ID = 716390832034414685
STAFF_ROLES = [
    718006431231508481,
    724879492622843944,
    732712709514199110,
    721825360827777043,
]


class Logging(commands.Cog):
    """For logging."""

    def __init__(self, bot):
        self.bot = bot

        self.log = logging.getLogger(f"Support")
        handler = logging.FileHandler(f"logs/support.log")
        handler.setFormatter(formatter)
        self.log.handlers = [handler]

        dlog = logging.getLogger("discord")
        dhandler = logging.FileHandler(f"logs/discord.log")
        dhandler.setFormatter(formatter)
        dlog.handlers = [dhandler]

        self.log.setLevel(logging.DEBUG)
        dlog.setLevel(logging.INFO)

    def serialize_channel(self, channel):
        base = {
            "id": channel.id,
            "type": str(channel.type),
            "name": channel.name,
            "position": channel.position,
        }
        if isinstance(channel, (discord.TextChannel, discord.VoiceChannel)):
            base["category_id"] = channel.category_id
        return base

    async def sync_guild(self, guild):
        await self.bot.mongo.db.guild.update_one(
            {"_id": guild.id},
            {
                "$set": {
                    "name": guild.name,
                    "icon": str(guild.icon_url),
                    "channels": [
                        self.serialize_channel(channel) for channel in guild.channels
                    ],
                }
            },
            upsert=True,
        )

    async def sync_member(self, member):
        roles = [member.guild.get_role(x) for x in STAFF_ROLES]
        role = discord.utils.find(lambda x: x in member.roles, roles)
        await self.bot.mongo.db.member.update_one(
            {"_id": member.id},
            {
                "$set": {
                    "name": member.name,
                    "discriminator": member.discriminator,
                    "nick": member.nick,
                    "avatar": str(member.avatar_url),
                    "role": None if role is None else role.name,
                }
            },
            upsert=True,
        )

    @commands.Cog.listener(name="on_guild_channel_create")
    @commands.Cog.listener(name="on_guild_channel_delete")
    @commands.Cog.listener(name="on_guild_channel_update")
    @commands.Cog.listener(name="on_guild_channel_update")
    @commands.Cog.listener(name="on_guild_join")
    @commands.Cog.listener(name="on_guild_update")
    async def on_guild_updates(self, *args):
        thing = args[-1]
        if not isinstance(thing, discord.Guild):
            thing = thing.guild
        await self.sync_guild(thing)

    @commands.Cog.listener(name="on_member_join")
    @commands.Cog.listener(name="on_member_update")
    @commands.Cog.listener(name="on_user_update")
    async def on_member_updates(self, *args):
        thing = args[-1]
        if isinstance(thing, discord.User):
            guild = self.bot.get_guild(GUILD_ID)
            thing = guild.get_member(thing.id)
        await self.sync_member(thing)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.guild is None:
            return
        time = int(message.created_at.timestamp())
        await self.bot.mongo.db.message.insert_one(
            {
                "_id": message.id,
                "user_id": message.author.id,
                "channel_id": message.channel.id,
                "guild_id": message.guild.id,
                "history": {str(time): message.content},
                "attachments": [
                    {"id": attachment.id, "filename": attachment.filename}
                    for attachment in message.attachments
                ],
                "deleted_at": None,
            }
        )

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload):
        if "content" not in payload.data:
            return
        time = int(datetime.utcnow().timestamp())
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
            {"$set": {"deleted_at": datetime.utcnow()}},
        )

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload):
        await self.bot.mongo.db.message.update_many(
            {"_id": {"$in": list(payload.message_ids)}},
            {"$set": {"deleted_at": datetime.utcnow()}},
        )


def setup(bot):
    bot.add_cog(Logging(bot))
