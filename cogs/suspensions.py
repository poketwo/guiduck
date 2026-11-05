import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional, Union

import discord
from discord.ext import commands, tasks

from helpers import checks
from helpers.utils import FetchUserConverter

RECONCILE_INTERVAL_MINUTES = 10
ID_BATCH_SIZE = 20000

# Mirrors NETWORK_SUSPENSIONS_PAUSED_KEY in poketwo's helpers/constants.py.
NETWORK_SUSPENSIONS_PAUSED_KEY = "network_suspensions:paused"

# Partial indexes on poketwo's `member` collection. Without them, listing every suspended account is a full scan,
# so the reconcile loop only lifts known suspensions (looked up by `_id`) until they exist.
POKETWO_SUSPENSION_INDEXES = {"suspended_partial", "suspended_until_partial", "network_suspension_partial"}
POKETWO_SUSPENSION_PROJECTION = {
    "suspended": 1,
    "suspended_until": 1,
    "network_suspension": 1,
    "network_suspension_exempt": 1,
}

CONFIG_PROJECTION = {
    "suspended_role_id": 1,
    "suspension_stripped_role_ids": 1,
    "booster_access_role_id": 1,
    "booster_access_source_role_ids": 1,
}

HideableChannel = Union[
    discord.CategoryChannel, discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.ForumChannel
]


def is_network_suspended(doc: dict, now: datetime) -> bool:
    suspension = doc.get("network_suspension")
    if not suspension or doc.get("network_suspension_exempt"):
        return False
    until = suspension.get("until")
    return until is None or until > now


def is_poketwo_suspended(doc: dict, now: datetime, network_paused: bool) -> bool:
    if doc.get("suspended") or (doc.get("suspended_until") and doc["suspended_until"] > now):
        return True
    return not network_paused and is_network_suspended(doc, now)


def batched(items: list, size: int = ID_BATCH_SIZE):
    for i in range(0, len(items), size):
        yield items[i : i + size]


class Suspensions(commands.Cog):
    """Mirrors Pokétwo suspensions (account, timed and network) onto a role.

    Suspended members get the configured suspended role and lose the configured "stripped" roles (e.g. Trading and
    the incense access roles) until the suspension ends; their levels, XP and other roles are untouched. Members
    holding a booster source role get the booster access role, unless they're suspended.

    Without the Members intent, members are checked when seen (`on_member_seen`) and by a reconcile loop that
    compares Pokétwo's suspended accounts with the members flagged in the `member` collection.
    """

    def __init__(self, bot):
        self.bot = bot
        self.locks = defaultdict(asyncio.Lock)
        self.reconcile_task.start()

    async def cog_load(self):
        # Don't await: index builds scan the whole collection and would block startup.
        self.index_task = asyncio.ensure_future(
            self.bot.mongo.db.member.create_index(
                [("_id.guild_id", 1), ("poketwo_suspended", 1)],
                partialFilterExpression={"poketwo_suspended": True},
            )
        )

    async def cog_unload(self):
        self.reconcile_task.cancel()

    # Config

    async def get_config(self, guild_id: int) -> dict:
        return await self.bot.mongo.db.guild.find_one({"_id": guild_id}, CONFIG_PROJECTION) or {}

    async def stripped_role_ids(self, guild_id: int) -> set[int]:
        return set((await self.get_config(guild_id)).get("suspension_stripped_role_ids", []))

    async def is_withheld(self, member: discord.Member, role_id: int) -> bool:
        """Whether the role is held back from the member because they're suspended."""

        if role_id not in await self.stripped_role_ids(member.guild.id):
            return False
        return await self.is_flagged(member.id, member.guild.id)

    # Pokétwo lookups

    async def network_suspensions_paused(self) -> bool:
        return bool(await self.bot.poketwo_redis.exists(NETWORK_SUSPENSIONS_PAUSED_KEY))

    async def fetch_suspended_ids(self, user_ids: list[int]) -> set[int]:
        """Which of the given users are suspended on Pokétwo. Looks them up by `_id`, so needs no extra index."""

        now = datetime.utcnow()
        network_paused = await self.network_suspensions_paused()
        suspended = set()
        for batch in batched(user_ids):
            async for doc in self.bot.mongo.poketwo_db.member.find(
                {"_id": {"$in": batch}}, POKETWO_SUSPENSION_PROJECTION
            ):
                if is_poketwo_suspended(doc, now, network_paused):
                    suspended.add(doc["_id"])
        return suspended

    async def has_suspension_indexes(self) -> bool:
        indexes = await self.bot.mongo.poketwo_db.member.index_information()
        return POKETWO_SUSPENSION_INDEXES <= indexes.keys()

    async def fetch_all_suspended_ids(self) -> set[int]:
        """Every suspended Pokétwo account. Only call this once POKETWO_SUSPENSION_INDEXES exist."""

        now = datetime.utcnow()
        collection = self.bot.mongo.poketwo_db.member
        suspended = set(await collection.distinct("_id", {"suspended": True}))
        suspended.update(await collection.distinct("_id", {"suspended_until": {"$gt": now}}))
        if not await self.network_suspensions_paused():
            async for doc in collection.find(
                {"network_suspension.kind": {"$exists": True}}, POKETWO_SUSPENSION_PROJECTION
            ):
                if is_network_suspended(doc, now):
                    suspended.add(doc["_id"])
        return suspended

    # Guiduck member state

    async def is_flagged(self, member_id: int, guild_id: int) -> bool:
        doc = await self.bot.mongo.db.member.find_one(
            {"_id": {"id": member_id, "guild_id": guild_id}, "poketwo_suspended": True}, {"_id": 1}
        )
        return doc is not None

    async def flagged_ids(self, guild_id: int) -> set[int]:
        return set(
            await self.bot.mongo.db.member.distinct("_id.id", {"_id.guild_id": guild_id, "poketwo_suspended": True})
        )

    async def cached_member_ids(self, guild_id: int, user_ids: list[int]) -> set[int]:
        """Which of the given users guiduck has seen in the guild, so people who never joined aren't fetched."""

        found = set()
        for batch in batched(user_ids):
            keys = [{"id": user_id, "guild_id": guild_id} for user_id in batch]
            async for doc in self.bot.mongo.db.member.find({"_id": {"$in": keys}}, {"_id": 1}):
                found.add(doc["_id"]["id"])
        return found

    async def set_flag(self, member_id: int, guild_id: int, removed_role_ids: list[int] = ()) -> Optional[dict]:
        """Flags the member as suspended, returning their document from before the update."""

        return await self.bot.mongo.db.member.find_one_and_update(
            {"_id": {"id": member_id, "guild_id": guild_id}},
            {
                "$set": {"poketwo_suspended": True},
                "$addToSet": {"suspension_removed_role_ids": {"$each": list(removed_role_ids)}},
            },
            upsert=True,
        )

    async def clear_flag(self, member_id: int, guild_id: int) -> Optional[dict]:
        """Clears the suspension flag, returning the member's document from before the update."""

        return await self.bot.mongo.db.member.find_one_and_update(
            {"_id": {"id": member_id, "guild_id": guild_id}, "poketwo_suspended": True},
            {"$unset": {"poketwo_suspended": 1, "suspension_removed_role_ids": 1}},
        )

    # Applying and lifting

    async def fetch_member(self, guild: discord.Guild, user_id: int) -> Optional[discord.Member]:
        """Fetches from the API, since the member cache goes stale without the Members intent."""

        try:
            return await guild.fetch_member(user_id)
        except discord.NotFound:
            return None

    async def apply(self, member: discord.Member, config: dict) -> bool:
        """Gives the suspended role and removes stripped roles. Returns whether anything changed."""

        suspended_role = member.guild.get_role(config.get("suspended_role_id"))
        stripped_ids = set(config.get("suspension_stripped_role_ids", []))
        to_remove = [role for role in member.roles if role.id in stripped_ids and role != suspended_role]

        # Flag first, so the levels cog stops re-adding stripped roles before they're removed.
        before = await self.set_flag(member.id, member.guild.id, [role.id for role in to_remove])
        changed = not (before or {}).get("poketwo_suspended", False)

        reason = "Suspended on Pokétwo"
        if suspended_role is not None and suspended_role not in member.roles:
            await member.add_roles(suspended_role, reason=reason)
            changed = True
        if to_remove:
            await member.remove_roles(*to_remove, reason=reason)
            changed = True
        return changed

    async def lift(self, member: discord.Member, config: dict):
        """Removes the suspended role and gives back the roles that were stripped."""

        before = await self.clear_flag(member.id, member.guild.id) or {}
        roles = {role.id for role in member.roles}
        restore_ids = set(before.get("suspension_removed_role_ids", [])) - roles

        trading_role = discord.utils.get(member.guild.roles, name="Trading")
        if before.get("trading_muted") and trading_role is not None:
            restore_ids.discard(trading_role.id)

        reason = "Pokétwo suspension ended"
        to_restore = [role for role in map(member.guild.get_role, restore_ids) if role is not None]
        if to_restore:
            await member.add_roles(*to_restore, reason=reason)

        suspended_role = member.guild.get_role(config.get("suspended_role_id"))
        if suspended_role is not None and suspended_role.id in roles:
            await member.remove_roles(suspended_role, reason=reason)

        if levels := self.bot.get_cog("Levels"):
            await levels.sync_level_roles(member)

    async def sync_booster_access(self, member: discord.Member, config: dict, suspended: bool):
        booster_access_role = member.guild.get_role(config.get("booster_access_role_id"))
        if booster_access_role is None:
            return

        source_ids = set(config.get("booster_access_source_role_ids", [])) - {booster_access_role.id}
        eligible = not suspended and any(role.id in source_ids for role in member.roles)
        has_role = booster_access_role in member.roles
        if eligible and not has_role:
            await member.add_roles(booster_access_role, reason="Booster access")
        elif has_role and not eligible:
            await member.remove_roles(
                booster_access_role, reason="Suspended on Pokétwo" if suspended else "No longer boosting"
            )

    async def enforce(self, member: discord.Member) -> Optional[str]:
        """Brings one member in line with Pokétwo. Returns "applied" or "lifted" if that happened."""

        if member.bot:
            return None
        config = await self.get_config(member.guild.id)
        if config.get("suspended_role_id") is None:
            return None

        async with self.locks[member.id]:
            suspended = member.id in await self.fetch_suspended_ids([member.id])
            result = None
            if suspended:
                if await self.apply(member, config):
                    result = "applied"
            elif await self.is_flagged(member.id, member.guild.id):
                await self.lift(member, config)
                result = "lifted"
            await self.sync_booster_access(member, config, suspended)
        return result

    # Logging

    async def log(self, guild: discord.Guild, message: str):
        data = await self.bot.mongo.db.guild.find_one({"_id": guild.id}, {"logs_channel_id": 1}) or {}
        channel = self.bot.get_channel(data.get("logs_channel_id"))
        if channel is not None:
            try:
                await channel.send(message, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                pass

    # Reconciling

    @tasks.loop(minutes=RECONCILE_INTERVAL_MINUTES)
    async def reconcile_task(self):
        async for data in self.bot.mongo.db.guild.find({"suspended_role_id": {"$exists": True}}, {"_id": 1}):
            guild = self.bot.get_guild(data["_id"])
            if guild is None:
                continue
            # Catch here: an unhandled error would stop the loop for good.
            try:
                await self.reconcile(guild)
            except Exception as error:
                self.bot.log.error(f"Suspension reconcile failed in {guild.id}: {error!r}")

    @reconcile_task.before_loop
    async def before_reconcile(self):
        await self.bot.wait_until_ready()

    async def reconcile(self, guild: discord.Guild) -> tuple[int, int, bool]:
        """Applies new suspensions and lifts ended ones. Returns (applied, lifted, whether new ones were checked)."""

        config = await self.get_config(guild.id)
        if config.get("suspended_role_id") is None:
            return 0, 0, False

        flagged = await self.flagged_ids(guild.id)
        still_suspended = await self.fetch_suspended_ids(list(flagged))
        lifted = 0
        for user_id in flagged - still_suspended:
            if await self.skip_on_http_error(user_id, self.reconcile_lift(guild, config, user_id)):
                lifted += 1

        applied = 0
        checked_new = await self.has_suspension_indexes()
        if checked_new:
            new_ids = await self.fetch_all_suspended_ids() - flagged
            for user_id in await self.cached_member_ids(guild.id, list(new_ids)):
                if await self.skip_on_http_error(user_id, self.reconcile_apply(guild, config, user_id)):
                    applied += 1

        await self.reconcile_booster_access(guild, config)

        if applied or lifted:
            await self.log(guild, f"Pokétwo suspension sync: applied to **{applied:,}**, lifted from **{lifted:,}**.")
        return applied, lifted, checked_new

    async def skip_on_http_error(self, user_id: int, coro) -> bool:
        """Runs one member's reconcile step, logging and skipping Discord errors so one bad request doesn't end the run."""

        try:
            return await coro
        except discord.HTTPException as error:
            self.bot.log.warning(f"Suspension reconcile skipped {user_id}: {error!r}")
            return False

    async def reconcile_lift(self, guild: discord.Guild, config: dict, user_id: int) -> bool:
        member = await self.fetch_member(guild, user_id)
        async with self.locks[user_id]:
            if member is None:
                await self.clear_flag(user_id, guild.id)
            else:
                await self.lift(member, config)
                await self.skip_on_http_error(user_id, self.sync_booster_access(member, config, False))
        return True

    async def reconcile_apply(self, guild: discord.Guild, config: dict, user_id: int) -> bool:
        member = await self.fetch_member(guild, user_id)
        async with self.locks[user_id]:
            if member is None:
                # Not in the server: flag only, so they aren't fetched every run. Roles go on when they're seen.
                await self.set_flag(user_id, guild.id)
                return False
            applied = await self.apply(member, config)
            await self.skip_on_http_error(user_id, self.sync_booster_access(member, config, True))
        return applied

    async def reconcile_booster_access_member(
        self, guild: discord.Guild, config: dict, member_id: int, suspended: bool
    ):
        if member := await self.fetch_member(guild, member_id):
            async with self.locks[member_id]:
                await self.sync_booster_access(member, config, suspended)

    async def reconcile_booster_access(self, guild: discord.Guild, config: dict):
        if guild.get_role(config.get("booster_access_role_id")) is None:
            return

        role_ids = [config["booster_access_role_id"], *config.get("booster_access_source_role_ids", [])]
        member_ids = await self.bot.mongo.db.member.distinct(
            "_id.id", {"_id.guild_id": guild.id, "roles": {"$in": role_ids}}
        )
        flagged = await self.flagged_ids(guild.id)
        for member_id in member_ids:
            await self.skip_on_http_error(
                member_id, self.reconcile_booster_access_member(guild, config, member_id, member_id in flagged)
            )

    # Listeners

    @commands.Cog.listener()
    async def on_member_join(self, member):
        await self.handle_seen(member)

    @commands.Cog.listener()
    async def on_member_seen(self, member, message):
        await self.handle_seen(member)

    async def handle_seen(self, member: discord.Member):
        result = await self.enforce(member)
        if result is not None:
            await self.log(member.guild, f"Pokétwo suspension role {result}: {member.mention} ({member.id})")

    # Commands

    async def update_config(self, ctx, update: dict):
        await self.bot.mongo.db.guild.update_one({"_id": ctx.guild.id}, update, upsert=True)

    def format_role(self, guild: discord.Guild, role_id: Optional[int]) -> str:
        role = guild.get_role(role_id) if role_id is not None else None
        return role.mention if role is not None else "*not set*"

    def format_roles(self, guild: discord.Guild, role_ids: list[int]) -> str:
        return ", ".join(self.format_role(guild, role_id) for role_id in role_ids) or "*none*"

    @commands.group(invoke_without_command=True, aliases=("suspensions",))
    @commands.guild_only()
    @checks.is_admin()
    async def suspension(self, ctx):
        """Shows the Pokétwo suspension role settings.

        You must have the Admin role to use this.
        """

        config = await self.get_config(ctx.guild.id)
        embed = discord.Embed(title="Pokétwo suspension role", color=discord.Color.blurple())
        embed.add_field(name="Suspended role", value=self.format_role(ctx.guild, config.get("suspended_role_id")))
        embed.add_field(
            name="Stripped while suspended",
            value=self.format_roles(ctx.guild, config.get("suspension_stripped_role_ids", [])),
            inline=False,
        )
        embed.add_field(
            name="Booster access role", value=self.format_role(ctx.guild, config.get("booster_access_role_id"))
        )
        embed.add_field(
            name="Booster access given to",
            value=self.format_roles(ctx.guild, config.get("booster_access_source_role_ids", [])),
            inline=False,
        )
        embed.set_footer(text=f"Members flagged as suspended: {len(await self.flagged_ids(ctx.guild.id)):,}")
        await ctx.send(embed=embed)

    @suspension.command(name="role")
    @commands.guild_only()
    @checks.is_admin()
    async def suspension_role(self, ctx, role: discord.Role):
        """Sets the role given to members suspended on Pokétwo.

        You must have the Admin role to use this.
        """

        await self.update_config(ctx, {"$set": {"suspended_role_id": role.id}})
        await ctx.send(f"Set the suspended role to **{role}**.")

    @suspension.group(name="strip", invoke_without_command=True)
    @commands.guild_only()
    @checks.is_admin()
    async def suspension_strip(self, ctx):
        """Manages the roles removed from suspended members and given back when the suspension ends.

        You must have the Admin role to use this.
        """

        await ctx.send_help(ctx.command)

    @suspension_strip.command(name="add")
    @commands.guild_only()
    @checks.is_admin()
    async def suspension_strip_add(self, ctx, roles: commands.Greedy[discord.Role]):
        """Adds roles to remove from suspended members.

        You must have the Admin role to use this.
        """

        if not roles:
            return await ctx.send("Please specify one or more roles.", ephemeral=True)
        await self.update_config(ctx, {"$addToSet": {"suspension_stripped_role_ids": {"$each": [r.id for r in roles]}}})
        await ctx.send(f"Suspended members will lose {', '.join(f'**{r}**' for r in roles)}.")

    @suspension_strip.command(name="remove")
    @commands.guild_only()
    @checks.is_admin()
    async def suspension_strip_remove(self, ctx, roles: commands.Greedy[discord.Role]):
        """Stops removing roles from suspended members.

        You must have the Admin role to use this.
        """

        if not roles:
            return await ctx.send("Please specify one or more roles.", ephemeral=True)
        await self.update_config(ctx, {"$pullAll": {"suspension_stripped_role_ids": [r.id for r in roles]}})
        await ctx.send(f"Suspended members will keep {', '.join(f'**{r}**' for r in roles)}.")

    @suspension.group(name="booster", invoke_without_command=True)
    @commands.guild_only()
    @checks.is_admin()
    async def suspension_booster(self, ctx):
        """Manages the booster access role, given to boosters who aren't suspended.

        You must have the Admin role to use this.
        """

        await ctx.send_help(ctx.command)

    @suspension_booster.command(name="role")
    @commands.guild_only()
    @checks.is_admin()
    async def suspension_booster_role(self, ctx, role: discord.Role):
        """Sets the booster access role.

        You must have the Admin role to use this.
        """

        await self.update_config(ctx, {"$set": {"booster_access_role_id": role.id}})
        await ctx.send(f"Set the booster access role to **{role}**.")

    @suspension_booster.command(name="add")
    @commands.guild_only()
    @checks.is_admin()
    async def suspension_booster_add(self, ctx, roles: commands.Greedy[discord.Role]):
        """Adds roles whose members get booster access (e.g. Server Booster).

        You must have the Admin role to use this.
        """

        if not roles:
            return await ctx.send("Please specify one or more roles.", ephemeral=True)
        await self.update_config(
            ctx, {"$addToSet": {"booster_access_source_role_ids": {"$each": [r.id for r in roles]}}}
        )
        await ctx.send(f"Members with {', '.join(f'**{r}**' for r in roles)} will get booster access.")

    @suspension_booster.command(name="remove")
    @commands.guild_only()
    @checks.is_admin()
    async def suspension_booster_remove(self, ctx, roles: commands.Greedy[discord.Role]):
        """Removes roles whose members get booster access.

        You must have the Admin role to use this.
        """

        if not roles:
            return await ctx.send("Please specify one or more roles.", ephemeral=True)
        await self.update_config(ctx, {"$pullAll": {"booster_access_source_role_ids": [r.id for r in roles]}})
        await ctx.send(f"Members with {', '.join(f'**{r}**' for r in roles)} no longer get booster access.")

    async def set_view_deny(self, ctx, channels: list[HideableChannel], hidden: bool):
        config = await self.get_config(ctx.guild.id)
        role = ctx.guild.get_role(config.get("suspended_role_id"))
        if role is None:
            return await ctx.send("Set the suspended role first with `suspension role`.", ephemeral=True)
        if not channels:
            return await ctx.send("Please specify one or more channels or categories.", ephemeral=True)

        verb = "Hidden" if hidden else "Unhidden"
        reason = f"{verb} for suspended members by {ctx.author} ({ctx.author.id})"
        lines = []
        for channel in channels:
            # The API doesn't propagate category overwrites, so update the channels synced to it like the client does.
            targets = [channel]
            unsynced = []
            if isinstance(channel, discord.CategoryChannel):
                targets += [c for c in channel.channels if c.permissions_synced]
                unsynced = [c for c in channel.channels if not c.permissions_synced]

            for target in targets:
                overwrite = target.overwrites_for(role)
                overwrite.view_channel = False if hidden else None
                await target.set_permissions(role, overwrite=None if overwrite.is_empty() else overwrite, reason=reason)

            line = f"{verb} {channel.mention}"
            if len(targets) > 1:
                line += f" and {len(targets) - 1} synced channel(s)"
            if unsynced:
                line += f". Not synced, so unchanged: {', '.join(c.mention for c in unsynced)}"
            lines.append(line)

        await ctx.send("\n".join(lines))

    @suspension.command(name="hide")
    @commands.guild_only()
    @checks.is_admin()
    async def suspension_hide(self, ctx, channels: commands.Greedy[HideableChannel]):
        """Denies View Channel to the suspended role in channels or categories.

        For a category, channels synced to it are updated too.
        You must have the Admin role to use this.
        """

        await self.set_view_deny(ctx, channels, True)

    @suspension.command(name="unhide")
    @commands.guild_only()
    @checks.is_admin()
    async def suspension_unhide(self, ctx, channels: commands.Greedy[HideableChannel]):
        """Removes the suspended role's View Channel deny from channels or categories.

        For a category, channels synced to it are updated too.
        You must have the Admin role to use this.
        """

        await self.set_view_deny(ctx, channels, False)

    @suspension.command(name="check")
    @commands.guild_only()
    @checks.is_admin()
    async def suspension_check(self, ctx, user: FetchUserConverter):
        """Shows a user's Pokétwo suspension and suspended role state, and syncs them if they're in the server.

        You must have the Admin role to use this.
        """

        doc = await self.bot.mongo.poketwo_db.member.find_one({"_id": user.id}, POKETWO_SUSPENSION_PROJECTION) or {}
        now = datetime.utcnow()
        network_paused = await self.network_suspensions_paused()
        member = await self.fetch_member(ctx.guild, user.id)
        result = await self.enforce(member) if member is not None else None

        state = []
        if doc.get("suspended"):
            state.append("permanently suspended")
        if doc.get("suspended_until") and doc["suspended_until"] > now:
            state.append(
                f"suspended until {discord.utils.format_dt(doc['suspended_until'].replace(tzinfo=timezone.utc))}"
            )
        if is_network_suspended(doc, now):
            state.append("network-suspended" + (" (network suspensions paused)" if network_paused else ""))

        lines = [
            f"**{user}** ({user.id})",
            f"Pokétwo: {', '.join(state) or 'not suspended'}",
            f"Flagged in guiduck: {'yes' if await self.is_flagged(user.id, ctx.guild.id) else 'no'}",
            f"In server: {'yes' if member is not None else 'no'}",
        ]
        if result is not None:
            lines.append(f"Suspended role **{result}** just now.")
        await ctx.send("\n".join(lines))

    @suspension.command(name="sync")
    @commands.guild_only()
    @checks.is_admin()
    async def suspension_sync(self, ctx):
        """Runs the suspension reconcile now instead of waiting for the loop.

        You must have the Admin role to use this.
        """

        async with ctx.typing():
            applied, lifted, checked_new = await self.reconcile(ctx.guild)
        message = f"Applied to **{applied:,}**, lifted from **{lifted:,}**."
        if not checked_new:
            message += (
                " New suspensions weren't checked: the partial indexes on Pokétwo's `member` collection are missing."
            )
        await ctx.send(message)


async def setup(bot):
    await bot.add_cog(Suspensions(bot))
