import logging
from datetime import datetime, timezone
import time
from urllib.parse import urlencode

import discord
from discord.ext import commands, tasks
from discord.utils import format_dt, time_snowflake
from pymongo import UpdateOne
import parsedatetime as pdt

from helpers import checks
from helpers.context import GuiduckContext
from helpers.converters import FetchChannelOrThreadConverter


class LogFlagConverter(commands.Converter):
    """
    Converter for logs command flags that support message IDs for filtration
    This accepts message link, message ID, "channel ID-message ID" or a date/time string.
    """

    async def convert(self, ctx: GuiduckContext, arg: str) -> discord.PartialMessage | int | datetime:
        try:
            message = await commands.PartialMessageConverter().convert(ctx, arg)
            return message
        except commands.MessageNotFound:
            try:
                return int(arg)
            except ValueError:
                calendar = pdt.Calendar()
                struct = calendar.parse(arg)[0]
                now = datetime.now()
                dt = datetime.fromtimestamp(time.mktime(struct))
                if 0 < (now - dt).total_seconds() < 1 :  # dt is current time when input is not valid
                    raise ValueError("Invalid input for before/after flag")

                return dt


class LogFlags(commands.FlagConverter, case_insensitive=True):
    channel: FetchChannelOrThreadConverter = commands.flag(description="The channel whose logs to show", default=None, positional=True)

    user: discord.Member | discord.User = commands.flag(description="Show logs of a specific user", default=None, aliases=("from",))
    before: LogFlagConverter = commands.flag(description="Filter logs before a specific message/time", default=None)
    after: LogFlagConverter = commands.flag(description="Filter logs after a specific message/time", default=None)
    limit: int = commands.flag(description="Limit how many logs to show (50 by default)", default=None)
    deleted: bool = commands.flag(description="Whether to show deleted messages only", default=None)

PARAM_OFFSETS = {"before": 1, "after": -1}

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

    async def bulk_write(self, collection_name: str, updates: list):
        collection = self.bot.mongo.db.get_collection(collection_name)
        if updates:
            await collection.bulk_write(updates)

    async def full_cache_guild(self, guild):
        await self.bulk_write("guild", [self.make_cache_guild(guild)])
        await self.bulk_write("channel", [self.make_cache_channel(channel) for channel in guild.channels])
        await self.bulk_write("channel", [self.make_cache_channel(channel) for channel in guild.threads])
        await self.bulk_write("member", [self.make_cache_member(member) for member in guild.members])

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
        }

        if isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.CategoryChannel)):
            base["position"] = channel.position
        if isinstance(channel, (discord.TextChannel, discord.VoiceChannel)):
            base["category_id"] = channel.category_id
        if isinstance(channel, (discord.TextChannel, discord.Thread)):
            base["last_message_id"] = channel.last_message_id
        if isinstance(channel, (discord.Thread)):
            base["parent_id"] = channel.parent_id

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
    @commands.Cog.listener(name="on_thread_create")
    @commands.Cog.listener(name="on_thread_update")
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

    @commands.hybrid_group(fallback="get")
    @checks.is_trial_moderator()
    @commands.guild_only()
    async def logs(
        self,
        ctx,
        *,
        flags: LogFlags,
    ):
        """Gets a link to the message logs for a channel.
        ### Supported Flags
        - `user`: Show logs of a specific user
        - `before`: Filter logs before a specific message/time
        - `after`: Filter logs after a specific message/time
        > These accept:
        > - Message Link
        > - Message ID (current channel)
        > - "ChannelID-MessageID" (retrieved by shift-clicking on “Copy ID”)
        > - Date/time string (e.g. `12/31 16:40`, `friday`, `yesterday`)
        - `limit`: Limit how many logs to show (50 by default)
        - `deleted`: Whether to show deleted messages only

        You must have the Trial Moderator role to use this.
        """

        channel = flags.channel or ctx.channel
        url = f"https://admin.poketwo.net/logs/{channel.guild.id}/{channel.id}"

        params = {}
        filter_texts = {}

        if flags.user:
            params["user"] = flags.user.id
            filter_texts["User"] = flags.user.mention

        if flags.before or flags.after:
            if flags.before and flags.after:
                raise commands.BadArgument("Both `before` and `after` flags cannot be used at the same time.")

            for param in ("before", "after"):
                value = getattr(flags, param)
                if not value:
                    continue

                value_line = None
                if isinstance(value, discord.PartialMessage):
                    value_line = value.jump_url

                    # Offset it to also include the provided message
                    offset = PARAM_OFFSETS.get(param, 0)
                    value = value.id + offset

                elif isinstance(value, datetime):
                    value_line = format_dt(value, 'F')
                    value = time_snowflake(value)

                params[param] = value
                if value_line:
                    filter_texts[param.title()] = value_line

        if flags.limit is not None:
            params["limit"] = flags.limit
            filter_texts["Limit"] = flags.limit

        if flags.deleted:
            params["deleted"] = flags.deleted
            filter_texts["Deleted Only"] = flags.deleted

        if params:
            url += f"?{urlencode(params)}"

        view = discord.ui.View()
        view.add_item(discord.ui.Button(label=f"Jump", url=url))

        lines = [f"### Message Logs of {channel.mention}", url]
        if filter_texts:
            lines.append("### Filters")
            lines.extend([f"- **{name}**: {text}" for name, text in filter_texts.items()])

        await ctx.send("\n".join(lines), view=view, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @logs.command()
    @commands.guild_only()
    @checks.is_community_manager()
    async def restrict(self, ctx, channel: discord.TextChannel | discord.Thread | discord.VoiceChannel = None):
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
