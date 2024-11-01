import collections
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from textwrap import dedent
from typing import Any, Dict, List, Literal, Optional

import discord
from bson.objectid import ObjectId
from discord.ext import commands

from data.models import Species
from helpers import checks, constants
from helpers.converters import SpeciesConverter
from helpers.converters import ActivityArgs
from helpers.outline.decorators import with_typing
from helpers.poketwo import format_pokemon_details
from helpers.utils import FetchUserConverter, as_line_chunks_by_len

REFUND_CHANNEL_ID = 973239955784614008

MANAGEMENT_LOGS_CHANNEL_ID = 1301202435380871230

IV_FLAGS = "iv_hp", "iv_atk", "iv_defn", "iv_satk", "iv_sdef", "iv_spd"
random_level = lambda ctx: max(1, min(int(random.normalvariate(20, 10)), 100))
random_nature = lambda ctx: random.choice(constants.POKEMON_NATURES)


def update(d, u):
    for k, v in u.items():
        if isinstance(v, collections.abc.Mapping):
            d[k] = update(d.get(k, {}), v)
        else:
            d[k] = v
    return d


def tabulate(data: List[List[str]]):
    """Function to tabulate data. The data"""

    ncols = len(data[0])
    header_lens = [max([len(str(d[i])) for d in data]) for i in range(ncols)]

    row_format = "| " + " | ".join(["{:<%s}" % header_lens[i] for i in range(ncols)]) + " |"
    border = "+-" + "-+-".join([f"{'':-<{hl}}" for hl in header_lens]) + "-+"

    table = [border, row_format.format(*data[0]), border, *[row_format.format(*row) for row in data[1:]], border]
    return "\n".join(table)


class PokemonRefundFlags(commands.FlagConverter, case_insensitive=True):
    species: Species = commands.flag(converter=SpeciesConverter)
    nature: Optional[str] = commands.flag(default=random_nature)
    level: int = commands.flag(default=random_level)
    xp: int = 0
    shiny: bool = False
    has_color: bool = commands.flag(aliases=("embedcolor",), default=False)

    iv_total: Optional[int] = commands.flag(aliases=("iv",))
    iv_hp: Optional[int] = commands.flag(aliases=("hpiv", "hp"))
    iv_atk: Optional[int] = commands.flag(aliases=("atkiv", "atk"))
    iv_defn: Optional[int] = commands.flag(aliases=("iv_def", "defiv", "def"))
    iv_satk: Optional[int] = commands.flag(aliases=("satkiv", "satk"))
    iv_sdef: Optional[int] = commands.flag(aliases=("sdefiv", "sdef"))
    iv_spd: Optional[int] = commands.flag(aliases=("spdiv", "spd"))

    notes: Optional[str]

    def resolve_iv_flags(self):
        for flag in IV_FLAGS:
            if not (getattr(self, flag) is None or 0 <= getattr(self, flag) <= 31):
                raise ValueError("Given IV flags are invalid")

        if not (self.iv_total is None or 0 <= self.iv_total <= 186):
            raise ValueError("Given IV flags are invalid")

        if self.iv_total is None:
            for flag in IV_FLAGS:
                if getattr(self, flag) is None:
                    setattr(self, flag, random.randint(0, 31))
            self.iv_total = sum(getattr(self, x) for x in IV_FLAGS)
            return

        given_points = sum(getattr(self, x) or 0 for x in IV_FLAGS)
        remaining_flags = [x for x in IV_FLAGS if getattr(self, x) is None]
        remaining_points = self.iv_total - given_points

        if remaining_points < 0 or remaining_points > len(remaining_flags) * 31:
            raise ValueError("Given IV flags are incompatible")

        for flag in remaining_flags:
            setattr(self, flag, 0)

        for i in range(remaining_points):
            flag = random.choice(remaining_flags)
            value = getattr(self, flag) + 1
            setattr(self, flag, value)
            if value == 31:
                remaining_flags.remove(flag)


@dataclass
class Refund:
    class AlreadyExecuted(Exception):
        pass

    user: discord.Member
    target: discord.Member
    jump_url: str = None
    notes: str = None

    pokecoins: Optional[int] = None
    shards: Optional[int] = None
    redeems: Optional[int] = None
    gifts_normal: Optional[int] = None
    gifts_great: Optional[int] = None
    gifts_ultra: Optional[int] = None
    gifts_master: Optional[int] = None
    pokemon_data: Optional[List[Dict[str, Any]]] = None

    pokemon: Optional[List[ObjectId]] = None
    _id: int = None
    _executed: bool = False

    def to_dict(self):
        base = {
            "user_id": self.user.id,
            "target_id": self.target.id,
        }
        for attr in (
            "notes",
            "pokecoins",
            "shards",
            "redeems",
            "gifts_normal",
            "gifts_great",
            "gifts_ultra",
            "gifts_master",
            "pokemon_data",
            "pokemon",
        ):
            if getattr(self, attr) is not None:
                base[attr] = getattr(self, attr)
        return base

    def to_embed(self, bot):
        contents = []

        # fmt: off
        if self.pokecoins:     contents.append(f"**Pokécoins:** {self.pokecoins}")
        if self.shards:        contents.append(f"**Shards:** {self.shards}")
        if self.redeems:       contents.append(f"**Redeems:** {self.redeems}")
        if self.gifts_normal:  contents.append(f"**Normal Boxes:** {self.gifts_normal}")
        if self.gifts_great:   contents.append(f"**Great Boxes:** {self.gifts_great}")
        if self.gifts_ultra:   contents.append(f"**Ultra Boxes:** {self.gifts_ultra}")
        if self.gifts_master:  contents.append(f"**Master Boxes:** {self.gifts_master}")
        # fmt: on

        if self.pokemon_data:
            contents.append("**Pokémon:**")
            for i, pokemon in enumerate(self.pokemon_data):
                if self.pokemon is not None:
                    pokemon["_id"] = self.pokemon[i]
                contents.extend(format_pokemon_details(bot, pokemon))

        embed = discord.Embed(
            title=f"Refunded {self.target} (ID: {self.target.id})",
            description="\n".join(contents),
            timestamp=datetime.now(timezone.utc),
            color=discord.Color.blurple(),
        )
        embed.set_author(name=f"{self.user} (ID: {self.user.id})", icon_url=self.user.display_avatar.url)
        embed.set_thumbnail(url=self.target.display_avatar.url)

        if self.notes is not None:
            embed.add_field(name="Notes", value=self.notes)

        return embed

    async def execute(self, bot):
        if self._executed:
            raise self.AlreadyExecuted()

        member_update = {}

        # fmt: off
        if self.pokecoins is not None:     update(member_update, {"$inc": {"balance": self.pokecoins}})
        if self.shards is not None:        update(member_update, {"$inc": {"premium_balance": self.shards}})
        if self.redeems is not None:       update(member_update, {"$inc": {"redeems": self.redeems}})
        if self.gifts_normal is not None:  update(member_update, {"$inc": {"gifts_normal": self.gifts_normal}})
        if self.gifts_great is not None:   update(member_update, {"$inc": {"gifts_great": self.gifts_great}})
        if self.gifts_ultra is not None:   update(member_update, {"$inc": {"gifts_ultra": self.gifts_ultra}})
        if self.gifts_master is not None:  update(member_update, {"$inc": {"gifts_master": self.gifts_master}})
        # fmt: on

        if len(member_update) > 0:
            await bot.mongo.poketwo_db.member.update_one({"_id": self.target.id}, member_update)
            await bot.poketwo_redis.hdel(f"db:member", self.target.id)

        if self.pokemon_data is not None:
            result = await bot.mongo.poketwo_db.pokemon.insert_many(self.pokemon_data)
            self.pokemon = result.inserted_ids

        self._executed = True


class PoketwoAdministration(commands.Cog):
    """For Pokétwo administration."""

    def __init__(self, bot):
        self.bot = bot

    async def save_refund(self, refund: Refund):
        if not refund._executed:
            raise ValueError("Can only save executed refunds")

        refund._id = await self.bot.mongo.reserve_id("refund")
        await self.bot.mongo.db.refund.insert_one({"_id": refund._id, **refund.to_dict()})

        channel = self.bot.get_channel(REFUND_CHANNEL_ID)
        view = discord.ui.View()
        if refund.jump_url is not None:
            view.add_item(discord.ui.Button(label="Jump", url=refund.jump_url))
        await channel.send(embed=refund.to_embed(self.bot), view=view)

    @commands.hybrid_group()
    @checks.support_server_only()
    @checks.is_moderator()
    async def refund(self, ctx):
        """Manages refunds of items and currencies to users.

        You must have the Moderator role to use this."""

        await ctx.send_help(ctx.command)

    @refund.command(aliases=("redeem", "deems", "deem"))
    @checks.support_server_only()
    @checks.is_moderator()
    async def redeems(self, ctx, member: discord.Member, number: int = 1, *, notes=None):
        """Refunds a given number of redeems to a user.

        You must have the Moderator role to use this.
        """

        refund = Refund(
            user=ctx.author,
            target=member,
            jump_url=ctx.message.jump_url,
            notes=notes,
            redeems=number,
        )

        await refund.execute(self.bot)
        await self.save_refund(refund)
        await ctx.send(embed=refund.to_embed(self.bot))

    @refund.command(aliases=("incense", "inc"))
    @checks.support_server_only()
    @checks.is_moderator()
    async def incenses(self, ctx, member: discord.Member, number: int = 1, *, notes=None):
        """Refunds a given number of incenses to a user.

        You must have the Moderator role to use this.
        """

        refund = Refund(
            user=ctx.author,
            target=member,
            jump_url=ctx.message.jump_url,
            notes=notes,
            shards=number * 50,
        )

        await refund.execute(self.bot)
        await self.save_refund(refund)
        await ctx.send(embed=refund.to_embed(self.bot))

    @refund.command(aliases=("pokecoin", "coins", "coin", "pc"))
    @checks.support_server_only()
    @checks.is_moderator()
    async def pokecoins(self, ctx, member: discord.Member, number: int, *, notes=None):
        """Refunds a given number of Pokécoins to a user.

        You must have the Moderator role to use this.
        """

        refund = Refund(
            user=ctx.author,
            target=member,
            jump_url=ctx.message.jump_url,
            notes=notes,
            pokecoins=number,
        )

        await refund.execute(self.bot)
        await self.save_refund(refund)
        await ctx.send(embed=refund.to_embed(self.bot))

    @refund.command(aliases=("shard",))
    @checks.support_server_only()
    @checks.is_moderator()
    async def shards(self, ctx, member: discord.Member, number: int, *, notes=None):
        """Refunds a given number of shards to a user.

        You must have the Moderator role to use this.
        """

        refund = Refund(
            user=ctx.author,
            target=member,
            jump_url=ctx.message.jump_url,
            notes=notes,
            shards=number,
        )

        await refund.execute(self.bot)
        await self.save_refund(refund)
        await ctx.send(embed=refund.to_embed(self.bot))

    @refund.command(aliases=("box",), usage="<member> <type> <number> [notes=None]")
    @checks.support_server_only()
    @checks.is_moderator()
    async def boxes(
        self,
        ctx,
        member: discord.Member,
        box_type: Literal["normal", "great", "ultra", "master"],
        number: int,
        *,
        notes=None,
    ):
        """Refunds a given number of a given type of box to a user.

        You must have the Moderator role to use this.
        """

        refund = Refund(
            user=ctx.author,
            target=member,
            jump_url=ctx.message.jump_url,
            notes=notes,
            **{f"gifts_{box_type}": number},
        )

        await refund.execute(self.bot)
        await self.save_refund(refund)
        await ctx.send(embed=refund.to_embed(self.bot))

    @refund.command(with_app_command=False)
    @checks.support_server_only()
    @checks.is_moderator()
    async def pokemon(self, ctx, member: discord.Member, *, flags: PokemonRefundFlags):
        """Refunds a Pokémon with given specifications to a user.

        You must have the Moderator role to use this.
        """

        try:
            flags.resolve_iv_flags()
        except ValueError as e:
            return await ctx.send(str(e))

        pokemon = {
            "owner_id": member.id,
            "owned_by": "user",
            "species_id": flags.species.id,
            "level": flags.level,
            "xp": flags.xp,
            "nature": flags.nature,
            "iv_hp": flags.iv_hp,
            "iv_atk": flags.iv_atk,
            "iv_defn": flags.iv_defn,
            "iv_satk": flags.iv_satk,
            "iv_sdef": flags.iv_sdef,
            "iv_spd": flags.iv_spd,
            "iv_total": flags.iv_total,
            "moves": [],
            "shiny": flags.shiny,
            "idx": await self.bot.mongo.fetch_next_idx(ctx.author),
        }

        refund = Refund(
            user=ctx.author,
            target=member,
            jump_url=ctx.message.jump_url,
            notes=flags.notes,
            pokemon_data=[pokemon],
        )

        await refund.execute(self.bot)
        await self.save_refund(refund)
        await ctx.send(embed=refund.to_embed(self.bot))

    def logs_embed(self, user: discord.User, target: str, title: str, description: str, notes: Optional[str] = None):
        embed = discord.Embed(
            title=f"{title} {target} (ID: {target.id})",
            description=description,
            timestamp=datetime.now(timezone.utc),
            color=discord.Color.blurple(),
        )
        embed.set_author(name=f"{user} (ID: {user.id})", icon_url=user.display_avatar.url)
        embed.set_thumbnail(url=target.display_avatar.url)

        if notes is not None:
            embed.add_field(name="Notes", value=notes)

        return embed

    @commands.hybrid_group(aliases=["manage", "management"], invoke_without_command=True)
    @commands.check_any(checks.is_server_manager(), checks.is_bot_manager())
    async def manager(self, ctx):
        """Management commands

        You must have the Server Manager or Bot Manager role to use this."""

        await ctx.send_help(ctx.command)

    @manager.command(aliases=("givecoins", "ac", "gc"))
    @commands.check_any(checks.is_server_manager(), checks.is_bot_manager())
    async def addcoins(self, ctx, user: FetchUserConverter, amt: int, *, notes: Optional[str] = None):
        """Add to a user's balance."""

        await self.bot.mongo.poketwo_db.member.update_one({"_id": user.id}, {"$inc": {"balance": amt}})
        await self.bot.poketwo_redis.hdel(f"db:member", user.id)

        await ctx.send(f"Gave **{user}** {amt:,} Pokécoins.")

        channel = self.bot.get_channel(MANAGEMENT_LOGS_CHANNEL_ID)
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Jump", url=ctx.message.jump_url))
        await channel.send(
            embed=self.logs_embed(ctx.author, user, "Gave pokécoins to", f"**Pokécoins:** {amt}", notes), view=view
        )

    @manager.command(aliases=("giveshard", "as", "gs"))
    @commands.check_any(checks.is_server_manager(), checks.is_bot_manager())
    async def addshards(self, ctx, user: FetchUserConverter, amt: int, *, notes: Optional[str] = None):
        """Add to a user's shard balance."""

        await self.bot.mongo.poketwo_db.member.update_one({"_id": user.id}, {"$inc": {"premium_balance": amt}})
        await self.bot.poketwo_redis.hdel(f"db:member", user.id)

        await ctx.send(f"Gave **{user}** {amt:,} shards.")

        channel = self.bot.get_channel(MANAGEMENT_LOGS_CHANNEL_ID)
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Jump", url=ctx.message.jump_url))
        await channel.send(
            embed=self.logs_embed(ctx.author, user, "Gave shards to", f"**Shards:** {amt}", notes), view=view
        )

    @manager.command(
        usage="[role: ROLE=Moderator] [users: USER1 USER2 ...] [month: MONTH=Previous] [year: YEAR=Current] [all-users: yes/no=no] [show-ids: yes/no=no]"
    )
    @checks.staff_categories_only()
    @checks.is_server_manager()
    @with_typing()
    async def activity(
        self,
        ctx,
        *,
        args: ActivityArgs,
    ):
        """Get ticket and bot-logs activity"""

        # Timeframe determination

        now = discord.utils.utcnow()

        from_month = args.month
        to_month = now.month

        from_year = args.year if args.year is not None else now.year
        to_year = from_year

        if from_month:
            # If user passed in a month value, should check logs from that month
            to_month = from_month + 1
            if to_month > 12:
                # Incase until_month exceeds 12, increase year and loop it forward
                to_year += 1
                to_month = to_month - from_month
        else:
            # If user passed in a month value, should check logs from previous month
            from_month = now.month - 1
            if from_month < 1:
                # Incase month goes below 1, decrease year and loop it backwards
                from_year -= 1
                from_month = 12

        from_dt = now.replace(year=from_year, month=from_month, day=1, hour=0, minute=0, second=0)
        to_dt = from_dt.replace(year=to_year, month=to_month)
        _filter = {"$gte": from_dt, "$lt": to_dt}

        # Members determination

        role = args.role
        if not (role or args.users or args.show_all):
            role = next((r for r_id in constants.MODERATOR_ROLES[-2:] if (r := ctx.guild.get_role(r_id))), None)

        members = []
        if role:
            members = role.members
        elif args.users:
            members = args.users

        if args.show_all:
            if args.users:
                raise ValueError("Can't use the 'all' flag with the 'users' flag!")

            bot_logs_member_ids = set(await self.bot.mongo.db.action.distinct("user_id", {"created_at": _filter}))
            tickets_member_ids = set(await self.bot.mongo.db.ticket.distinct("agent_id", {"closed_at": _filter}))
            members.extend(
                [
                    self.bot.get_user(mid) or await self.bot.fetch_user(mid)
                    for mid in bot_logs_member_ids | tickets_member_ids
                ]
            )

        if not members:
            return await ctx.send("Role/users not found.")

        priv_vars = await self.bot.mongo.fetch_private_variable("activity")

        cols = priv_vars["columns"]
        bnet = priv_vars["bot_logs_net"]
        tnet = priv_vars["tickets_net"]
        max_amount = priv_vars["max_amount"]
        min_total = priv_vars["min_total"]

        net = lambda b, t: b * bnet + t * tnet
        data = []
        for member in set(members):
            tickets = await self.bot.mongo.db.ticket.count_documents({"agent_id": member.id, "closed_at": _filter})
            bot_logs = await self.bot.mongo.db.action.count_documents(
                {"user_id": member.id, "created_at": _filter}
                | (
                    {"type": {"$nin": ["untimeout", "unmute", "trading_unmute", "unban"]}}
                    if member == self.bot.user
                    else {}
                )
            )
            total = net(bot_logs, tickets)

            raw = round(total * 100)
            amount = min(max_amount, raw if total >= min_total else 0)

            MAX_NAME_LENGTH = 13
            name = (
                (member.name[:MAX_NAME_LENGTH] + ("..." if len(member.name) > MAX_NAME_LENGTH else ""))
                if not args.show_ids
                else str(member.id)
            )
            if tickets or bot_logs:
                data.append(
                    [
                        name + ("*" if role and member not in role.members else ""),
                        bot_logs,
                        tickets,
                        total,
                        raw,
                        amount,
                    ]
                )

        data.sort(key=lambda t: t[4], reverse=True)

        if len(members) > 1:
            data.append(["" for _ in cols])
            data.append(
                ["TOTAL", *[sum([d[i] if not isinstance(d[i], str) else 0 for d in data]) for i in range(1, len(cols))]]
            )

        table = tabulate([cols, *data])
        msgs = [
            dedent(
                f"""
                ### Number of Bot Logs And Tickets By {role.mention if role else members[0].mention if len(members) == 1 else f'{len(members)} Users'}
                No. of actions in #bot-logs and tickets (both SS and OS, *latest agent only*) by {members[0].name if len(members) == 1 else f"each user"} in {from_dt:%B}
                > **From**: {discord.utils.format_dt(from_dt)}
                > **To**: {discord.utils.format_dt(to_dt)}
                > **Min Cut-off**: {min_total}
                > **Formula**: `(bot-logs * {bnet} + tickets * {tnet}) * 100`
                > **Max Amount**: {max_amount}"""
            ),
            *[
                f"""{"`"*3}py\n{chunk}\n{"`"*3}"""
                for chunk in as_line_chunks_by_len(table, constants.CONTENT_CHAR_LIMIT - 10)
            ],  # - 10 because of the code block
        ]

        if len("\n".join(msgs)) < constants.CONTENT_CHAR_LIMIT:
            msgs = ["\n".join(msgs)]

        for i, msg in enumerate(msgs):
            await (ctx.reply if i == 0 else ctx.send)(
                msg,
                mention_author=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )


async def setup(bot):
    await bot.add_cog(PoketwoAdministration(bot))
