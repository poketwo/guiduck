import asyncio
import logging
from datetime import date, datetime, timezone
import math
import time
from urllib.parse import urlencode
from typing import Optional

import discord
from cryptography.fernet import Fernet, InvalidToken
from discord.ext import commands, tasks
from discord.utils import format_dt, time_snowflake
from pymongo import UpdateOne
import parsedatetime as pdt

import config
from helpers import checks
from helpers.context import GuiduckContext
from helpers.converters import FetchChannelOrThreadConverter
from helpers.pagination import PageNavigation


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
                if 0 < (now - dt).total_seconds() < 1:  # dt is current time when input is not valid
                    raise ValueError("Invalid input for before/after flag")

                return dt


class LogFlags(commands.FlagConverter, case_insensitive=True):
    channel: FetchChannelOrThreadConverter = commands.flag(
        description="The channel whose logs to show", default=None, positional=True
    )
    no_embed: bool = commands.flag(
        name="no-embed",
        description="Don't embed the logs directly in the channel. Default False.",
        aliases=["n"],
        default=False,
    )
    ephemeral: bool = commands.flag(description="Hide the message shown (default True)", default=True)

    user: discord.Member | discord.User = commands.flag(
        description="Show logs of a specific user", default=None, aliases=("from",)
    )
    before: LogFlagConverter = commands.flag(description="Filter logs before a specific message/time", default=None)
    after: LogFlagConverter = commands.flag(description="Filter logs after a specific message/time", default=None)
    limit: int = commands.flag(description="Limit how many logs to show (50 by default)", default=None)
    deleted: bool = commands.flag(description="Whether to show deleted messages only", default=None)


PARAM_OFFSETS = {"before": 1, "after": -1}
MESSAGE_RETENTION_DAYS = 30
LOGS_PER_PAGE = 10
LOGS_TEXT_LIMIT = 3900  # Components V2 messages are capped at 4000 display characters


def fit_cap(lengths: list[int], budget: int) -> Optional[int]:
    """Largest per-item length cap so that the capped lengths fit in the budget, or None if no cap is needed."""

    if sum(lengths) <= budget:
        return None
    lo, hi = 0, max(lengths)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if sum(min(n, mid) for n in lengths) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return lo


def log_timestamp(dt: datetime) -> str:
    """Time-only timestamp, with the date prepended when it isn't today."""

    if dt.date() == discord.utils.utcnow().date():
        return format_dt(dt, "t")
    return f"{format_dt(dt, 'd')} {format_dt(dt, 't')}"


def truncate(text: str, cap: Optional[int]) -> str:
    if cap is None or len(text) <= cap:
        return text
    return text[: max(cap - 1, 0)].rstrip("\\") + "…"


class LogsView(discord.ui.LayoutView):
    """Components V2 view for message logs: a header with a dashboard link, and optionally paginated log entries."""

    def __init__(self, ctx: GuiduckContext, *, header: str, url: str, get_page=None, timeout: int = 120):
        super().__init__(timeout=timeout if get_page else None)
        self.ctx = ctx
        self.header = header
        self.url = url
        self.get_page = get_page
        self.page = 0
        self.num_pages = None
        self.entries = []
        self.nav = None
        self.message = None

    @property
    def footer(self) -> str:
        total = "?" if self.num_pages is None else self.num_pages
        return f"-# ✏️ Edited · ❌ Deleted · 📎 Attachments · Page {self.page + 1}/{total}"

    def build(self):
        self.clear_items()
        container = discord.ui.Container(accent_colour=discord.Colour.blurple())
        container.add_item(
            discord.ui.Section(
                discord.ui.TextDisplay(self.header),
                accessory=discord.ui.Button(label="Dashboard", url=self.url),
            )
        )

        if self.get_page:
            container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
            for i, entry in enumerate(self.entries or ["*No messages found.*"]):
                if i:
                    container.add_item(discord.ui.Separator())
                container.add_item(discord.ui.TextDisplay(entry))
            container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
            container.add_item(discord.ui.TextDisplay(self.footer))

            self.nav = PageNavigation()
            at_start = self.page == 0
            at_end = self.num_pages is not None and self.page + 1 >= self.num_pages
            self.nav.first.disabled = self.nav.previous.disabled = at_start
            self.nav.next.disabled = at_end
            self.nav.last.disabled = self.num_pages is None or at_end
            if not (at_start and at_end):
                container.add_item(self.nav)

        self.add_item(container)

    async def load(self, page: int):
        budget = LOGS_TEXT_LIMIT - len(self.header) - len(self.footer)
        self.entries = await self.get_page(page, budget)
        self.page = page
        self.build()

    async def start(self, **kwargs):
        if self.get_page:
            await self.load(0)
        else:
            self.build()
        self.message = await self.ctx.reply(view=self, mention_author=False, **kwargs)

    async def show_page(self, interaction: discord.Interaction, page: int):
        if page == self.page or page < 0:
            return await interaction.response.defer()
        await self.load(page)
        try:
            await interaction.response.edit_message(view=self)
        except (discord.NotFound, discord.InteractionResponded):
            if self.message:
                await self.message.edit(view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("You can't use this!", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        if self.message and self.nav:
            for button in self.nav.children:
                button.disabled = True
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class Logging(commands.Cog):
    """For logging."""

    def __init__(self, bot):
        self.bot = bot
        self.log = logging.getLogger("support")
        self.fernet = Fernet(config.MESSAGE_LOG_KEY)
        self.cache_all.start()

    def encrypt(self, content: str) -> str:
        return self.fernet.encrypt(content.encode()).decode()

    def decrypt(self, content: str) -> str:
        try:
            return self.fernet.decrypt(content.encode()).decode()
        except InvalidToken:  # stored before encryption was added
            return content

    async def cog_load(self):
        # Don't await: building the index scans the whole (huge) collection and would block startup.
        self.index_task = asyncio.ensure_future(
            self.bot.mongo.db.message.create_index("created_at", expireAfterSeconds=MESSAGE_RETENTION_DAYS * 86400)
        )

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
            "permissions": role.permissions.value,
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
        if isinstance(channel, discord.abc.GuildChannel):
            base["overwrites"] = [
                {"id": ow.id, "type": ow.type, "allow": ow.allow, "deny": ow.deny} for ow in channel._overwrites
            ]
        if isinstance(channel, (discord.TextChannel, discord.VoiceChannel)):
            base["category_id"] = channel.category_id
        if isinstance(channel, (discord.TextChannel, discord.Thread)):
            base["last_message_id"] = channel.last_message_id
        if isinstance(channel, (discord.Thread)):
            base["parent_id"] = channel.parent_id

        return UpdateOne({"_id": channel.id}, {"$set": base}, upsert=True)

    def make_cache_member(self, member, role_ids=None):
        return UpdateOne(
            {"_id": {"id": member.id, "guild_id": member.guild.id}},
            {
                "$set": {
                    "name": member.name,
                    "discriminator": member.discriminator,
                    "nick": member.nick,
                    "avatar": str(member.display_avatar.url),
                    "roles": list(role_ids) if role_ids is not None else [x.id for x in member.roles],
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
    async def on_member_seen(self, member, message):
        """Keeps the member cache fresh, in case member join/update events are missed."""

        await self.bot.mongo.db.member.bulk_write([self.make_cache_member(member)])

    @commands.Cog.listener()
    async def on_user_update(self, _, new):
        for guild in self.bot.guilds:
            member = guild.get_member(new.id)
            if member is None:
                continue
            await self.bot.mongo.db.member.bulk_write([self.make_cache_member(member)])

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if before.channel == after.channel:
            return

        if before.channel:
            await before.channel.send(
                f"**{member.mention}** has left the voice channel.", allowed_mentions=discord.AllowedMentions.none()
            )

        if after.channel:
            await after.channel.send(
                f"**{member.mention}** has joined the voice channel.", allowed_mentions=discord.AllowedMentions.none()
            )

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
                "created_at": message.created_at,
                "history": {str(time): self.encrypt(message.content)},
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
            {"$set": {f"history.{time}": self.encrypt(payload.data["content"])}},
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
        - `no-embed`: Don't embed the logs directly in the channel. Default False.
        - `ephemeral`: Hide the shown message (default True)

        - `user`: Show logs of a specific user
        - `before`: Filter logs before a specific message/time
        > This flag accepts:
        > - Message Link
        > - Message ID (current channel)
        > - "ChannelID-MessageID" (retrieved by shift-clicking on “Copy ID”)
        > - Date/time string (e.g. `12/31 16:40`, `friday`, `yesterday`)
        - `after`: Filter logs after a specific message/time. Same inputs as `before`.
        - `limit`: Limit how many logs to show (50 by default)
        - `deleted`: Whether to show deleted messages only

        You must have the Trial Moderator role to use this.
        """

        channel = flags.channel or ctx.channel
        try:
            member = channel.guild.get_member(ctx.author.id) or await channel.guild.fetch_member(ctx.author.id)
        except discord.NotFound:
            member = None
        if member is None or not channel.permissions_for(member).view_channel:
            raise commands.BadArgument("You can't view that channel.")
        if channel.type == discord.ChannelType.private_thread and not channel.permissions_for(member).manage_threads:
            try:
                await channel.fetch_member(member.id)
            except discord.NotFound:
                raise commands.BadArgument("You can't view that channel.")

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
                    value_line = format_dt(value, "F")
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

        lines = [f"### Message Logs of {channel.mention}", f"-# Channel ID: {channel.id}"]
        if filter_texts:
            lines.extend([f"- **{name}**: {text}" for name, text in filter_texts.items()])
        header = "\n".join(lines)

        if flags.no_embed:
            view = LogsView(ctx, header=header, url=url)
            return await view.start(ephemeral=flags.ephemeral, allowed_mentions=discord.AllowedMentions.none())

        filt = {"channel_id": channel.id}
        if params.get("user"):
            filt["user_id"] = params.get("user")
        if params.get("deleted"):
            filt["deleted_at"] = {"$ne": None}

        for f, op in zip(("before", "after"), ("$lte", "$gte")):
            if params.get(f):
                filt.setdefault("_id", dict())[op] = params[f]

        def render(m, versions):
            dt = discord.utils.snowflake_time(m["_id"])
            tags = "`❌`" * bool(m.get("deleted_at")) + "`✏️`" * (len(versions) > 1) + "`📎`" * bool(m.get("attachments"))
            text = f"[{log_timestamp(dt)}]{tags} <@{m['user_id']}>: {versions[0][1] or '*No text content*'}"
            for ts, content in versions[1:]:
                edit_dt = datetime.fromtimestamp(ts + 3600, tz=timezone.utc)  # stored an hour early
                text += f"\n> [{log_timestamp(edit_dt)}] {content or '*No text content*'}"
            return text

        async def get_page(page: int, budget: int) -> list[str]:
            cursor = self.bot.mongo.db.message.find(filt).sort("_id", -1).skip(page * LOGS_PER_PAGE)
            docs = await cursor.limit(LOGS_PER_PAGE).to_list(None)
            if len(docs) < LOGS_PER_PAGE:
                view.num_pages = page + 1

            versions = []
            for m in docs:
                history = sorted(m["history"].items(), key=lambda t: -int(t[0]))
                escaped = [(int(k), discord.utils.escape_markdown(self.decrypt(v))) for k, v in history]
                versions.append([escaped[0]] + [(k, v.replace("\n", "\n> ")) for k, v in escaped[1:]])

            entries = list(zip(docs, versions))
            overhead = sum(len(render(m, [(k, "") for k, _ in vs])) for m, vs in entries)
            lengths = [len(v) for _, vs in entries for _, v in vs]
            cap = fit_cap(lengths or [0], budget - overhead)
            return [render(m, [(k, truncate(v, cap)) for k, v in vs]) for m, vs in entries]

        view = LogsView(ctx, header=header, url=url, get_page=get_page)
        await view.start(ephemeral=flags.ephemeral, allowed_mentions=discord.AllowedMentions.none())

    @logs.command(name="sync-cache")
    @commands.guild_only()
    @checks.is_community_manager()
    async def sync_cache(self, ctx):
        """Syncs all caches for the current server.

        You must have the Community Manager role to use this."""

        await ctx.send("Syncing all caches for this guild...")
        await self.full_cache_guild(ctx.guild)
        await ctx.send("Completed cache sync.")

    @checks.is_trial_moderator()
    @commands.guild_only()
    @commands.hybrid_command()
    async def snipe(
        self,
        ctx,
        channel: Optional[discord.TextChannel | discord.Thread | discord.VoiceChannel] = commands.CurrentChannel,
        nth: int = 1,
    ):
        permissions = channel.permissions_for(ctx.author)
        if not all([getattr(permissions, perm, False) for perm in ("read_messages", "read_message_history")]):
            return await ctx.reply("You don't have permissions to view that channel.")

        if nth <= 0:
            return await ctx.reply("The nth deleted message to show cannot be 0 or less.", mention_author=False)

        message = await self.bot.mongo.db.message.find_one(
            {"channel_id": channel.id, "deleted_at": {"$ne": None}}, sort=[("_id", -1)], skip=nth - 1
        )
        if not message:
            return await ctx.reply("No deleted message found.", mention_author=False)

        user = ctx.guild.get_member(message["user_id"]) or await self.bot.fetch_user(message["user_id"])
        content = self.decrypt(list(message["history"].values())[-1])

        embed = discord.Embed(description=content, color=getattr(user, "color", None))
        embed.set_author(name=f"{user.display_name} ({user.id})", icon_url=user.display_avatar.url)
        embed.set_footer(text=f"{message['_id']} • #{channel.name}")
        embed.timestamp = message["deleted_at"]

        await ctx.reply(embed=embed, mention_author=False)

    async def cog_unload(self):
        self.cache_all.cancel()


async def setup(bot):
    await bot.add_cog(Logging(bot))
