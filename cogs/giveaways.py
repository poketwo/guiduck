import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import NamedTuple, Optional

import discord
from bson.objectid import ObjectId
from discord.ext import commands, tasks
from pymongo.errors import DuplicateKeyError

from helpers.poketwo import format_pokemon, format_pokemon_details
from helpers.utils import FakeUser


MAX_PENDING_GIVEAWAYS = 5


@dataclass
class Giveaway:
    duration = timedelta(hours=12)

    bot: commands.Bot
    guild_id: int
    user: discord.Member
    pokemon_ids: list[ObjectId]
    approval_status: Optional[bool] = None
    description: Optional[str] = None
    _id: Optional[ObjectId] = None

    channel_id: Optional[int] = None
    message_id: Optional[int] = None
    ends_at: Optional[datetime] = None
    winner: Optional[discord.Member] = None

    _pokemon: Optional[list[dict]] = None
    _message: Optional[discord.Message] = None

    @property
    def guild(self):
        return self.bot.get_guild(self.guild_id)

    @property
    def channel(self):
        if self.channel_id is None:
            return None
        return self.guild.get_channel(self.channel_id)

    async def title(self):
        return ", ".join(format_pokemon(self.bot, x) for x in await self.pokemon)

    @property
    async def message(self):
        if self.message_id is None:
            return None
        if self._message is None:
            self._message = await self.channel.fetch_message(self.message_id)
        return self._message

    @property
    async def pokemon(self):
        if self._pokemon is None:
            cursor = self.bot.mongo.poketwo_db.pokemon.find({"_id": {"$in": self.pokemon_ids}})
            self._pokemon = await cursor.to_list(None)
        return self._pokemon

    @classmethod
    def build_from_mongo(cls, bot, x):
        guild = bot.get_guild(x["guild_id"])
        user = guild.get_member(x["user_id"]) or FakeUser(x["user_id"])
        kwargs = {
            "bot": bot,
            "_id": x["_id"],
            "guild_id": x["guild_id"],
            "user": user,
            "pokemon_ids": x["pokemon_ids"],
            "approval_status": x.get("approval_status"),
            "description": x.get("description"),
            "channel_id": x.get("channel_id"),
            "message_id": x.get("message_id"),
            "ends_at": x.get("ends_at"),
        }
        if winner_id := x.get("winner_id"):
            kwargs["winner"] = guild.get_member(winner_id) or FakeUser(winner_id)
        return cls(**kwargs)

    def to_dict(self):
        base = {
            "guild_id": self.guild_id,
            "user_id": self.user.id,
            "pokemon_ids": self.pokemon_ids,
        }
        if self.approval_status is not None:
            base["approval_status"] = self.approval_status
        if self.description is not None:
            base["description"] = self.description
        if self.channel_id is not None:
            base["channel_id"] = self.channel_id
        if self.message_id is not None:
            base["message_id"] = self.message_id
        if self.ends_at is not None:
            base["ends_at"] = self.ends_at
        if self.winner is not None:
            base["winner_id"] = self.winner.id
        return base

    async def approval_embed(self):
        embed = discord.Embed(
            title="Giveaway Request Pending",
            description="\n".join(i for x in await self.pokemon for i in format_pokemon_details(self.bot, x)),
            color=discord.Color.blurple(),
        )
        embed.set_author(name=str(self.user), icon_url=self.user.display_avatar.url)
        if self.description is not None:
            embed.add_field(name="Message", value=self.description)
        return embed

    async def giveaway_embed(self):
        num_entries = await self.bot.mongo.db.giveaway_entry.count_documents({"giveaway_id": self._id})

        details = [
            f"Ends: {discord.utils.format_dt(self.ends_at, 'R')} ({discord.utils.format_dt(self.ends_at, 'f')})",
            f"Entries: {num_entries}",
        ]

        embed = discord.Embed(
            title=await self.title(),
            description="\n".join(i for x in await self.pokemon for i in format_pokemon_details(self.bot, x)),
            timestamp=self.ends_at,
        )
        embed.set_author(name=str(self.user), icon_url=self.user.display_avatar.url)

        if self.ends_at < datetime.now(timezone.utc):
            embed.set_footer(text="Ended at")
            if self.winner is None:
                details.append("Winner: No one")
            else:
                details.append(f"Winner: {self.winner.mention}")
        else:
            embed.color = discord.Color.blurple()
            embed.set_footer(text="Ends at")

        embed.add_field(name="Details", value="\n".join(details))

        pokemon, *_ = await self.pokemon
        if pokemon.get("shiny"):
            embed.set_thumbnail(url=f"https://cdn.poketwo.net/shiny/{pokemon['species_id']}.png")
        else:
            embed.set_thumbnail(url=f"https://cdn.poketwo.net/images/{pokemon['species_id']}.png")

        return embed

    async def submit_for_approval(self):
        """Submits a giveaway for approval, persisting it in the database."""

        guild_data = await self.bot.mongo.db.guild.find_one({"_id": self.guild_id})
        if guild_data is None or guild_data.get("giveaway_approval_channel_id") is None:
            raise ValueError("Guild does not have giveaways set up.")

        for pokemon_id in self.pokemon_ids:
            pokemon = await self.bot.mongo.poketwo_db.pokemon.find_one(
                {"owned_by": "user", "owner_id": self.user.id, "_id": pokemon_id}
            )
            if pokemon is None:
                raise ValueError(f"Couldn't find the pokemon with ID {pokemon_id}!")

        channel = self.bot.get_channel(guild_data["giveaway_approval_channel_id"])

        result = await self.bot.mongo.db.giveaway.insert_one(self.to_dict())
        self._id = result.inserted_id
        await self.send_pokemon_to_escrow()
        await channel.send(embed=await self.approval_embed(), view=GiveawayApprovalView(self))

    async def send_pokemon_to_escrow(self):
        """Sends the Pokémon to escrow."""

        await self.bot.mongo.poketwo_db.pokemon.update_many(
            {"_id": {"$in": self.pokemon_ids}}, {"$set": {"owned_by": "giveaway"}}
        )

    async def send_pokemon_to_user(self, user: discord.Member):
        """Sends the Pokémon to a user."""

        idx = await self.bot.mongo.fetch_next_idx(user, reserve=len(self.pokemon_ids))
        for pokemon_id in self.pokemon_ids:
            await self.bot.mongo.poketwo_db.pokemon.update_one(
                {"_id": pokemon_id},
                {"$set": {"idx": idx, "owned_by": "user", "owner_id": user.id}},
            )
            idx += 1

    async def start(self, channel: discord.TextChannel):
        """Starts a giveaway, sending the message and adding the button."""

        self.ends_at = datetime.now(timezone.utc) + self.duration
        message = await channel.send(
            f'<@&721875438879768742> {self.user.mention} is giving away the following Pokémon and says "{self.description}"! Click \N{PARTY POPPER} to enter!',
            embed=await self.giveaway_embed(),
            view=GiveawayJoinView(self),
            allowed_mentions=discord.AllowedMentions(roles=[discord.Object(721875438879768742)]),
        )
        self.message_id = message.id
        self.channel_id = channel.id
        self._message = None

        await self.bot.mongo.db.giveaway.update_one(
            {"_id": self._id},
            {"$set": {"ends_at": self.ends_at, "channel_id": channel.id, "message_id": message.id}},
        )

    async def end(self):
        """Ends a giveaway."""

        if winner := await self.bot.mongo.db.giveaway_entry.aggregate(
            [
                {"$match": {"giveaway_id": self._id}},
                {"$sample": {"size": 1}},
            ],
            allowDiskUse=True,
        ).to_list(1):
            self.winner = self.guild.get_member(winner[0]["user_id"]) or FakeUser(winner[0]["user_id"])
            await self.bot.mongo.db.giveaway.update_one({"_id": self._id}, {"$set": {"winner_id": self.winner.id}})
            await self.send_pokemon_to_user(self.winner)
            await self.channel.send(
                f"Congratulations {self.winner.mention}! You have won **{self.user}'s {await self.title()}**!"
            )

            embed = await self.approval_embed()
            embed.title = "Giveaway Won!"
            return await self.winner.send(
                f"Congratulations! You have won **{self.user}'s {await self.title()}**!", embed=embed
            )

        await self.send_pokemon_to_user(self.user)
        await self.bot.mongo.db.giveaway.update_one({"_id": self._id}, {"$set": {"winner_id": None}})
        await self.channel.send("No one entered the giveaway. Better luck next time!")
        await self.update_embed()
        await self.user.send(
            f"No one entered your giveaway for **{await self.title()}**. You have received the Pokémon back."
        )

    async def update_embed(self):
        """Updates the giveaway embed."""

        if await self.bot.redis.get(f"giveaway_embed_update:{self._id}") is not None:
            return
        await self.bot.redis.set(f"giveaway_embed_update:{self._id}", 1, expire=2)

        if message := await self.message:
            if self.ends_at < datetime.now(timezone.utc):
                await message.edit(embed=await self.giveaway_embed(), view=None)
            else:
                await message.edit(embed=await self.giveaway_embed())


class GiveawayApproveButton(discord.ui.Button):
    def __init__(self, giveaway: Giveaway):
        super().__init__(
            style=discord.ButtonStyle.green,
            label="Approve",
            custom_id=f"persistent:giveaway:approve:{giveaway._id}",
        )
        self.bot = giveaway.bot
        self.giveaway = giveaway

    async def callback(self, interaction: discord.Interaction):
        await self.bot.mongo.db.giveaway.update_one({"_id": self.giveaway._id}, {"$set": {"approval_status": True}})

        embed = await self.giveaway.approval_embed()
        embed.title = "Giveaway Request Approved"
        embed.color = discord.Color.green()
        await interaction.response.edit_message(embed=embed, view=None)
        await self.giveaway.user.send(embed=embed)


class GiveawayDenyButton(discord.ui.Button):
    def __init__(self, giveaway: Giveaway):
        super().__init__(
            style=discord.ButtonStyle.red,
            label="Deny",
            custom_id=f"persistent:giveaway:deny:{giveaway._id}",
        )
        self.bot = giveaway.bot
        self.giveaway = giveaway

    async def callback(self, interaction: discord.Interaction):
        await self.bot.mongo.db.giveaway.update_one({"_id": self.giveaway._id}, {"$set": {"approval_status": False}})
        await self.giveaway.send_pokemon_to_user(self.giveaway.user)

        embed = await self.giveaway.approval_embed()
        embed.title = "Giveaway Request Denied"
        embed.color = discord.Color.red()
        await interaction.response.edit_message(embed=embed, view=None)
        await self.giveaway.user.send(embed=embed)


class GiveawayJoinButton(discord.ui.Button):
    def __init__(self, giveaway: Giveaway):
        super().__init__(label="\N{PARTY POPPER}", custom_id=f"persistent:giveaway:join:{giveaway._id}")
        self.bot = giveaway.bot
        self.giveaway = giveaway

    async def callback(self, interaction: discord.Interaction):
        if self.giveaway.ends_at < datetime.now(timezone.utc):
            return await interaction.response.send_message("This giveaway has already ended!", ephemeral=True)

        try:
            await self.bot.mongo.db.giveaway_entry.insert_one(
                {"giveaway_id": self.giveaway._id, "user_id": interaction.user.id}
            )
        except DuplicateKeyError:
            await interaction.response.send_message("You have already joined this giveaway!", ephemeral=True)
        else:
            await interaction.response.send_message("You have joined this giveaway!", ephemeral=True)
        await self.giveaway.update_embed()


class GiveawayApprovalView(discord.ui.View):
    def __init__(self, giveaway: Giveaway):
        super().__init__()
        self.giveaway = giveaway
        self.stop()
        self.add_item(GiveawayApproveButton(giveaway))
        self.add_item(GiveawayDenyButton(giveaway))


class GiveawayJoinView(discord.ui.View):
    def __init__(self, giveaway: Giveaway):
        super().__init__()
        self.giveaway = giveaway
        self.stop()
        self.add_item(GiveawayJoinButton(giveaway))


class DispatchedGiveaway(NamedTuple):
    giveaway: Giveaway
    task: asyncio.Task


class Giveaways(commands.Cog):
    """For giveaways."""

    def __init__(self, bot):
        self.bot = bot
        self.start_giveaways.start()
        self._current: Optional[DispatchedGiveaway] = None
        self.bot.loop.create_task(self.update_current())

    async def cog_load(self):
        await self.bot.mongo.db.giveaway.create_index([("approval_status", 1), ("ends_at", 1), ("_id", 1)])
        await self.bot.mongo.db.giveaway.create_index([("winner_id", 1), ("ends_at", 1), ("channel_id", 1)])
        await self.bot.mongo.db.giveaway_entry.create_index([("giveaway_id", 1), ("user_id", 1)], unique=True)

    async def cog_unload(self):
        self.start_giveaways.cancel()
        self.clear_current()

    async def fetch_giveaway(self, _id: str | ObjectId):
        if not isinstance(_id, ObjectId):
            _id = ObjectId(_id)
        data = await self.bot.mongo.db.giveaway.find_one({"_id": _id})
        if data is not None:
            return Giveaway.build_from_mongo(self.bot, data)

    def validate_minimum_requirements(self, p):
        species = self.bot.data.species_by_number(p["species_id"])
        iv_total = p["iv_hp"] + p["iv_atk"] + p["iv_defn"] + p["iv_satk"] + p["iv_sdef"] + p["iv_spd"]
        conditions = [
            any((species.mythical, species.legendary, species.ultra_beast, "-alola" in species.slug, "-galar" in species.slug, "-hisui" in species.slug, "-paldea" in species.slug)) and iv_total >= 112,
            species.event and iv_total >= 112,
            iv_total >= 168 or iv_total <= 18,
            p.get("shiny"),
        ]
        return any(conditions)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return

        custom_id = interaction.data["custom_id"]
        if custom_id.startswith("persistent:giveaway:approve:"):
            button_cls = GiveawayApproveButton
            giveaway_id = custom_id.removeprefix("persistent:giveaway:approve:")
        elif custom_id.startswith("persistent:giveaway:deny:"):
            button_cls = GiveawayDenyButton
            giveaway_id = custom_id.removeprefix("persistent:giveaway:deny:")
        elif custom_id.startswith("persistent:giveaway:join:"):
            button_cls = GiveawayJoinButton
            giveaway_id = custom_id.removeprefix("persistent:giveaway:join:")
        else:
            return

        giveaway = await self.fetch_giveaway(giveaway_id)
        if giveaway is not None:
            await button_cls(giveaway).callback(interaction)
        else:
            await interaction.response.send_message("Could not find that giveaway!")

    @commands.hybrid_group(fallback="start")
    async def giveaway(self, ctx, pokemon: int, *, message):
        """Start a giveaway."""

        user_pending_count = await self.bot.mongo.db.giveaway.count_documents({"user_id": ctx.author.id, "approval_status": None})
        if user_pending_count >= MAX_PENDING_GIVEAWAYS:
            return await ctx.reply(f"You already have the max number of giveways pending ({MAX_PENDING_GIVEAWAYS}). Please try again once those have been reviewed!")

        pokemon = await self.bot.mongo.poketwo_db.pokemon.find_one(
            {"owned_by": "user", "owner_id": ctx.author.id, "idx": pokemon}
        )
        if pokemon is None:
            return await ctx.send("Couldn't find that pokemon!", ephemeral=True)

        if not self.validate_minimum_requirements(pokemon):
            return await ctx.send("That Pokémon doesn't meet the minimum requirements to give away!", ephemeral=True)

        giveaway = Giveaway(
            bot=self.bot,
            guild_id=ctx.guild.id,
            user=ctx.author,
            pokemon_ids=[pokemon["_id"]],
            description=message,
        )

        if pokemon.get("favorite"):
            content = "**Warning:** This Pokémon is favorited. Make sure you want to give it away!"
        else:
            content = None

        if not await ctx.confirm(content=content, embed=await giveaway.approval_embed(), ephemeral=True):
            return await ctx.send("Aborted.", ephemeral=True)

        try:
            await giveaway.submit_for_approval()
        except ValueError as err:
            return await ctx.send(str(err), ephemeral=True)
        else:
            await ctx.send("Your giveaway has been submitted for approval.", ephemeral=True)

    async def get_next_giveaway(self):
        if giveaway := await self.bot.mongo.db.giveaway.find_one(
            {"ends_at": {"$exists": True}, "winner_id": {"$exists": False}},
            sort=[("ends_at", 1)],
        ):
            return Giveaway.build_from_mongo(self.bot, giveaway)

    def clear_current(self):
        if self._current is None:
            return
        self._current.task.cancel()
        self._current = None

    async def update_current(self, giveaway: Optional[Giveaway] = None):
        await self.bot.wait_until_ready()

        if giveaway is None:
            giveaway = await self.get_next_giveaway()
            if giveaway is None:
                return

        if self._current is not None and not self._current.task.done():
            if giveaway.ends_at > self._current.giveaway.ends_at:
                return
            self.clear_current()

        self._current = DispatchedGiveaway(
            giveaway=giveaway,
            task=self.bot.loop.create_task(self.dispatch_giveaway(giveaway)),
        )

    async def dispatch_giveaway(self, giveaway: Giveaway):
        try:
            await discord.utils.sleep_until(giveaway.ends_at)
        except asyncio.CancelledError:
            return

        await giveaway.end()
        await giveaway.update_embed()
        self.bot.loop.create_task(self.update_current())

    @tasks.loop(seconds=30)
    async def start_giveaways(self):
        async for giveaway in self.bot.mongo.db.giveaway.find({"approval_status": True, "ends_at": {"$exists": False}}):
            giveaway = Giveaway.build_from_mongo(self.bot, giveaway)
            guild_data = await self.bot.mongo.db.guild.find_one({"_id": giveaway.guild_id})
            guild = self.bot.get_guild(giveaway.guild_id)

            for channel_id in guild_data.get("giveaway_channel_ids", []):
                query = {"channel_id": channel_id, "ends_at": {"$exists": True}, "winner_id": {"$exists": False}}
                if await self.bot.mongo.db.giveaway.find_one(query) is None:
                    break
            else:
                return

            channel = guild.get_channel(channel_id)
            await giveaway.start(channel)
            self.bot.loop.create_task(self.update_current(giveaway))

    @start_giveaways.before_loop
    async def before_check_giveaways(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Giveaways(bot))
