from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import cached_property
from typing import Optional, Sequence, Type

import discord
from discord.ext import commands
from helpers import checks, constants
from helpers.utils import FakeUser

ALL_CATEGORIES: Sequence[Type[HelpDeskCategory]] = {}
TICKETS_CHANNEL_ID = 932520611564122153
NOTIFY_CHANNEL = 932520629087899658


@dataclass
class Ticket(abc.ABC):
    bot: commands.Bot
    _id: str
    user: discord.Member
    category: HelpDeskCategory
    guild_id: int
    channel_id: int
    thread_id: int
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    closed_at: Optional[datetime] = None
    agent: Optional[discord.Member] = None
    embedded_id: Optional[int] = None

    @property
    def guild(self):
        return self.bot.get_guild(self.guild_id)

    @property
    def thread(self):
        return self.guild.get_thread(self.thread_id)

    @classmethod
    def build_from_mongo(cls, bot, x):
        guild = bot.get_guild(x["guild_id"])
        user = guild.get_member(x["user_id"]) or FakeUser(x["user_id"])
        kwargs = {
            "bot": bot,
            "_id": x["_id"],
            "user": user,
            "category": ALL_CATEGORIES[x["category"]],
            "guild_id": x["guild_id"],
            "channel_id": x["channel_id"],
            "thread_id": x["thread_id"],
            "created_at": x["created_at"],
            "closed_at": x.get("closed_at"),
            "embedded_id": x.get("embedded_id"),
        }
        if "agent_id" in x:
            kwargs["agent"] = guild.get_member(x["agent_id"]) or FakeUser(x["agent_id"])
        return cls(**kwargs)

    def to_dict(self):
        base = {
            "user_id": self.user.id,
            "category": self.category.id,
            "guild_id": self.guild_id,
            "channel_id": self.channel_id,
            "thread_id": self.thread_id,
            "embedded_id": self.embedded_id,
            "created_at": self.created_at,
        }
        if self.closed_at is not None:
            base["closed_at"] = self.closed_at
        if self.agent is not None:
            base["agent_id"] = self.agent.id
        if self.embedded_id is not None:
            base["embedded_id"] = self.embedded_id
        return base

    def to_first_embed(self):
        embed = discord.Embed(
            title="Ticket Created",
            description=(
                "Please explain your query in detail so that our team can assist you promptly and effectively. Our support team has been notified and an agent will assist you soon.\n\n"
                "We usually respond to support tickets within 24 hours; however, note that responses may be delayed during busy intervals. Thank you for your patience.\n\n"
                "If you created this ticket on accident or no longer need assistance, please close the ticket by clicking the :lock: **Close Ticket** button below this message."
            ),
            color=discord.Color.blurple(),
            timestamp=self.created_at,
        )
        embed.add_field(name="Category", value=self.category.label)
        return embed

    def to_notify_embed(self):
        embed = discord.Embed(
            title=f"Opened {self._id} ({self.category.label})",
            color=discord.Color.blurple(),
        )
        embed.set_author(name=str(self.user), icon_url=self.user.display_avatar.url)
        if self.agent is not None:
            embed.color = discord.Color.gold()
            embed.add_field(name="Agent", value=self.agent.mention)
        if self.closed_at is not None:
            embed.color = discord.Embed.Empty
            embed.set_footer(text="Ticket Closed")
            embed.timestamp = self.closed_at
        return embed

    async def save(self):
        channel = self.guild.get_channel(NOTIFY_CHANNEL)
        if self.embedded_id is None:
            embedded = await channel.send(embed=self.to_notify_embed(), view=NotifyView(self))
            self.embedded_id = embedded.id
        else:
            embedded = await channel.fetch_message(self.embedded_id)
            await embedded.edit(embed=self.to_notify_embed(), view=NotifyView(self))

        await self.bot.mongo.db.ticket.update_one({"_id": self._id}, {"$set": self.to_dict()}, upsert=True)

    async def close(self):
        self.closed_at = datetime.now(timezone.utc)
        await self.thread.edit(archived=True, locked=True)
        await self.save()

    async def claim(self, user: discord.Member):
        self.agent = user
        await self.thread.add_user(user)
        await self.save()

    async def add(self, user: discord.Member):
        await self.thread.add_user(user)


class CloseTicketButton(discord.ui.Button):
    def __init__(self, ticket: Ticket):
        super().__init__(
            label="Close",
            emoji="\N{LOCK}",
            custom_id=f"persistent:ticket:close:{ticket._id}",
        )
        self.ticket = ticket

    async def callback(self, interaction: discord.Interaction):
        if interaction.user == self.ticket.user or any(
            x.id in constants.MODERATOR_ROLES for x in interaction.user.roles
        ):
            await self.ticket.close()


class ClaimTicketButton(discord.ui.Button):
    def __init__(self, ticket: Ticket):
        super().__init__(
            label="Claim",
            emoji="\N{TRIANGULAR FLAG ON POST}",
            custom_id=f"persistent:ticket:claim:{ticket._id}",
            style=discord.ButtonStyle.primary,
            disabled=ticket.closed_at is not None or ticket.agent is not None,
        )
        self.ticket = ticket

    async def callback(self, interaction: discord.Interaction):
        if any(x.id in constants.MODERATOR_ROLES for x in interaction.user.roles):
            await self.ticket.claim(interaction.user)
            await interaction.response.defer()


class JoinTicketButton(discord.ui.Button):
    def __init__(self, ticket: Ticket):
        super().__init__(
            label="Join Thread",
            custom_id=f"persistent:ticket:join:{ticket._id}",
            disabled=ticket.closed_at is not None,
        )
        self.ticket = ticket

    async def callback(self, interaction: discord.Interaction):
        if any(x.id in constants.MODERATOR_ROLES for x in interaction.user.roles):
            await self.ticket.add(interaction.user)
            await interaction.response.defer()


class FirstView(discord.ui.View):
    def __init__(self, ticket: Ticket):
        super().__init__()
        self.stop()
        self.add_item(CloseTicketButton(ticket))


class NotifyView(discord.ui.View):
    def __init__(self, ticket: Ticket):
        super().__init__()
        self.stop()
        self.add_item(ClaimTicketButton(ticket))
        self.add_item(JoinTicketButton(ticket))


class HelpDeskCategory(abc.ABC):
    bot: commands.Bot
    id: str
    label: str
    description: str
    emoji: str

    def __init__(self, bot):
        self.bot = bot

    def __init_subclass__(cls):
        ALL_CATEGORIES[cls.id] = cls

    # @abc.abstractmethod
    async def callback(self, interaction: discord.Interaction):
        await self.open_ticket(interaction)

    async def open_ticket(self, interaction: discord.Interaction):
        guild = self.bot.get_guild(constants.SUPPORT_SERVER_ID)
        channel = guild.get_channel(TICKETS_CHANNEL_ID)

        _id = f"{self.id.upper()} {await self.bot.mongo.reserve_id(f'ticket_{self.id}'):03}"
        thread = await channel.create_thread(
            name=_id,
            type=discord.ChannelType.private_thread,
            reason=f"Created support ticket for {interaction.user}",
        )
        ticket = Ticket(
            bot=self.bot,
            _id=_id,
            user=interaction.user,
            category=self,
            guild_id=guild.id,
            channel_id=channel.id,
            thread_id=thread.id,
            created_at=discord.utils.snowflake_time(interaction.id),
        )

        await ticket.save()

        first_view = FirstView(ticket)
        await thread.add_user(interaction.user)
        await thread.send(embed=ticket.to_first_embed(), view=first_view)


class SetupHelp(HelpDeskCategory):
    id = "setup"
    label = "Setup Help"
    description = (
        "Help with setting up the bot, configuring spawn channels, changing the prefix, permissions, etc."
    )
    emoji = "\N{GEAR}\ufe0f"


class GeneralQuestions(HelpDeskCategory):
    id = "gen"
    label = "General Pokétwo Questions"
    description = "Questions about command usage, trading, or how the bot works in general."
    emoji = "\N{INFORMATION SOURCE}\ufe0f"


class BugReports(HelpDeskCategory):
    id = "bug"
    label = "Bug Reports"
    description = "Report issues that looks like bugs or unintended behavior."
    emoji = "\N{BUG}"


class Reports(HelpDeskCategory):
    id = "rpt"
    label = "User Reports"
    description = "Report users violating the Pokétwo Terms of Service."
    emoji = "\N{NO ENTRY SIGN}"


class IncenseRefunds(HelpDeskCategory):
    id = "inc"
    label = "Incense Refunds"
    description = "Bot went down in the middle of an incense? Request a refund here."
    emoji = "\N{CANDLE}\ufe0f"


class StorePurchases(HelpDeskCategory):
    id = "store"
    label = "Store Purchases"
    description = "Payment methods, unreceived items, refunds, disputes, etc."
    emoji = "\N{MONEY WITH WINGS}"


class Punishments(HelpDeskCategory):
    id = "pun"
    label = "Bans & Suspensions"
    description = "Ban lengths, ban reasons, how to appeal, etc."
    emoji = "\N{HAMMER}"


class Miscellaneous(HelpDeskCategory):
    id = "misc"
    label = "Other Support Inquiries"
    description = (
        "For questions that do not fit the above categories, choose this option to talk to a staff member."
    )
    emoji = "\N{BLACK QUESTION MARK ORNAMENT}"


class HelpDeskSelect(discord.ui.Select):
    def __init__(self, bot):
        self.categories = [cls(bot) for cls in ALL_CATEGORIES.values()]
        super().__init__(
            placeholder="Select Option",
            custom_id="persistent:help_desk_select",
            options=[
                discord.SelectOption(label=cat.label, value=cat.id, emoji=cat.emoji)
                for cat in self.categories
            ],
        )
        self.bot = bot

    @cached_property
    def categories_by_id(self):
        return {x.id: x for x in self.categories}

    async def callback(self, interaction: discord.Interaction):
        await self.categories_by_id[self.values[0]].callback(interaction)


class HelpDeskView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot
        self.select = HelpDeskSelect(bot)
        self.add_item(self.select)

    @property
    def text(self):
        return "\n\n".join(
            [
                "**Welcome to the Pokétwo Help Desk!**",
                "Please select the appropriate option for the type of support you're looking for.",
                *[
                    f"{category.emoji}  **{category.label}**\n{category.description}"
                    for category in self.select.categories
                ],
            ]
        )


class HelpDesk(commands.Cog):
    """For the help desk on the support server."""

    def __init__(self, bot):
        self.bot = bot
        self.bot.loop.create_task(self.setup_view())

    async def setup_view(self):
        await self.bot.wait_until_ready()
        self.view = HelpDeskView(self.bot)
        self.bot.add_view(self.view)

    async def fetch_ticket_by_id(self, _id):
        ticket = await self.bot.mongo.db.ticket.find_one({"_id": _id})
        if ticket is not None:
            return Ticket.build_from_mongo(self.bot, ticket)

    async def fetch_ticket_by_thread(self, thread_id):
        ticket = await self.bot.mongo.db.ticket.find_one({"thread_id": thread_id})
        if ticket is not None:
            return Ticket.build_from_mongo(self.bot, ticket)

    @commands.command()
    @commands.is_owner()
    async def makedesk(self, ctx):
        await ctx.send(self.view.text, view=self.view)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return

        custom_id = interaction.data["custom_id"]
        if custom_id.startswith("persistent:ticket:close"):
            button_cls = CloseTicketButton
            ticket_id = custom_id.removeprefix("persistent:ticket:close:")
        elif custom_id.startswith("persistent:ticket:claim"):
            button_cls = ClaimTicketButton
            ticket_id = custom_id.removeprefix("persistent:ticket:claim:")
        elif custom_id.startswith("persistent:ticket:join"):
            button_cls = JoinTicketButton
            ticket_id = custom_id.removeprefix("persistent:ticket:join:")
        else:
            return

        ticket = await self.fetch_ticket_by_id(ticket_id)
        if ticket is not None:
            await button_cls(ticket).callback(interaction)
            await interaction.response.defer()
        else:
            await interaction.response.send_message("Could not find that ticket!")

    @commands.command()
    @checks.support_server_only()
    async def close(self, ctx):
        ticket = await self.fetch_ticket_by_thread(ctx.channel.id)
        if ticket is None:
            return await ctx.send("Could not find a ticket in this channel!")

        if ctx.author == ticket.user or any(x.id in constants.MODERATOR_ROLES for x in ctx.author.roles):
            await ctx.send("Closing ticket...")
            await ticket.close()
        else:
            await ctx.send("You do not have permission to do that!")

    def cog_unload(self):
        self.view.stop()


def setup(bot):
    bot.add_cog(HelpDesk(bot))
