import collections
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

import discord
from bson.objectid import ObjectId
from discord.ext import commands

from data.models import Species
from helpers import checks, constants
from helpers.converters import SpeciesConverter

REFUND_CHANNEL_ID = 973239955784614008

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
                species = bot.data.species_by_number(pokemon["species_id"]).name
                shiny = "\N{SPARKLES} " if pokemon["shiny"] else ""
                level = pokemon["level"]
                iv = pokemon["iv_total"] / 186
                iv_distr = " / ".join(str(pokemon[x]) for x in IV_FLAGS)
                contents.append(f"\N{EN DASH} Level {level} {shiny}{species}")
                contents.append(f"\N{IDEOGRAPHIC SPACE}\N{EN DASH} IV: {iv_distr} ({iv:.2%})")
                if self.pokemon is not None:
                    contents.append(f"\N{IDEOGRAPHIC SPACE}\N{EN DASH} ID: {self.pokemon[i]}")

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

    @commands.group(invoke_without_command=True)
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

    @refund.command()
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


async def setup(bot):
    await bot.add_cog(PoketwoAdministration(bot))
