from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

from helpers import checks, constants
from helpers.utils import as_line_chunks_by_len

# Roles that put someone on poketwo.net/people. The site maps these IDs to titles.
STAFF_ROLE_IDS = [
    974942298301431809,  # Admin
    718006431231508481,  # Admin
    1472581910894215268,  # Advisor
    1150516551368314900,  # Event Team Lead
    1150516638236553319,  # Development Lead
    1150517456230694932,  # Human Resources Lead
    1483962728866644109,  # Manager
    1483963509275623444,  # Manager
    1196632752523579423,  # Developer
    1120600250474827856,  # Developer
    974937010462683166,  # Moderator
    724879492622843944,  # Moderator
    930776361205919804,  # Event Team
]

AUDIT_LOG_LIMIT = 100
AVATAR_SIZE = 256
MESSAGE_CHAR_LIMIT = 2000


class StaffRoster(commands.Cog):
    """Keeps a live list of staff members in `staff_roster` for poketwo.net/people.

    The Members intent isn't available, so candidates come from the `member` cache (refreshed whenever
    someone talks), the previous roster and recent role grants in the audit log. Each candidate is then
    fetched from the API (never the gateway cache, which goes stale without the intent) so people who
    left or lost their roles drop off.
    """

    def __init__(self, bot):
        self.bot = bot
        self.refresh_task.start()

    async def cog_unload(self):
        self.refresh_task.cancel()

    @tasks.loop(hours=1)
    async def refresh_task(self):
        await self.refresh()

    @refresh_task.before_loop
    async def before_refresh(self):
        await self.bot.wait_until_ready()

    async def find_candidate_ids(self, guild):
        candidates = set(
            await self.bot.mongo.db.member.distinct(
                "_id.id", {"_id.guild_id": guild.id, "roles": {"$in": STAFF_ROLE_IDS}}
            )
        )

        if previous := await self.bot.mongo.db.staff_roster.find_one({"_id": guild.id}):
            candidates.update(int(x["id"]) for x in previous["members"])

        try:
            async for entry in guild.audit_logs(
                limit=AUDIT_LOG_LIMIT, action=discord.AuditLogAction.member_role_update
            ):
                if any(r.id in STAFF_ROLE_IDS for r in getattr(entry.after, "roles", [])):
                    candidates.add(entry.target.id)
        except discord.Forbidden:
            pass

        return candidates

    def make_entry(self, member):
        roles = sorted((r for r in member.roles if r.id in STAFF_ROLE_IDS), key=lambda r: -r.position)
        if member.bot or not roles:
            return None
        return {
            "id": str(member.id),
            "name": member.name,
            "display_name": member.display_name,
            "avatar": member.display_avatar.with_size(AVATAR_SIZE).url,
            "roles": [{"id": str(r.id), "name": r.name} for r in roles],
        }

    async def refresh(self):
        guild = self.bot.get_guild(constants.COMMUNITY_SERVER_ID)
        if guild is None:
            return None

        entries = []
        for user_id in await self.find_candidate_ids(guild):
            try:
                member = await guild.fetch_member(user_id)
            except discord.NotFound:
                continue
            if entry := self.make_entry(member):
                entries.append(entry)

        entries.sort(key=lambda x: x["name"])
        await self.bot.mongo.db.staff_roster.replace_one(
            {"_id": guild.id},
            {"updated_at": datetime.now(timezone.utc), "members": entries},
            upsert=True,
        )
        return entries

    @commands.command(aliases=("staffroster",))
    @checks.is_admin()
    async def staff_roster(self, ctx):
        """Refreshes the staff roster used by poketwo.net/people and shows the result."""

        async with ctx.typing():
            entries = await self.refresh()
        if entries is None:
            return await ctx.send("Community server not found.")
        lines = [f"{len(entries)} staff:"]
        lines += [f"{x['name']} — {' / '.join(r['name'] for r in x['roles'])}" for x in entries]
        for chunk in as_line_chunks_by_len("\n".join(lines), MESSAGE_CHAR_LIMIT):
            await ctx.send(chunk)


async def setup(bot):
    await bot.add_cog(StaffRoster(bot))
