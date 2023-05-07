from __future__ import annotations

import abc
import contextlib
import textwrap
from dataclasses import MISSING, dataclass, field
from datetime import datetime, timedelta, timezone
from functools import cached_property
from typing import Optional, Type

import discord
from discord.ext import commands

from helpers import checks, constants, time
from helpers.utils import FakeUser

ALL_CATEGORIES: dict[str, Type[HelpDeskCategory]] = {}

TICKETS_CHANNEL_ID = 932520611564122153

STATUS_CATEGORY_ID = 934619199047864330
STATUS_CHANNEL_ID_NEW = 932520629087899658
STATUS_CHANNEL_ID_OPEN = 944431717492600862
STATUS_CHANNEL_ID_CLOSED = 938621530190008401


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
    subject: Optional[str] = None
    description: Optional[str] = None
    agent: Optional[discord.Member] = None

    status_channel_id: Optional[int] = STATUS_CHANNEL_ID_NEW
    status_message_id: Optional[int] = None

    @property
    def guild(self):
        return self.bot.get_guild(self.guild_id)

    @property
    def thread(self):
        return self.guild.get_thread(self.thread_id)

    @property
    def status_channel(self):
        if self.status_channel_id is None:
            return None
        return self.guild.get_channel(self.status_channel_id)

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
            "subject": x.get("subject"),
            "description": x.get("description"),
            "status_channel_id": x.get("status_channel_id"),
            "status_message_id": x.get("status_message_id"),
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
            "created_at": self.created_at,
        }
        if self.closed_at is not None:
            base["closed_at"] = self.closed_at
        if self.subject is not None:
            base["subject"] = self.subject
        if self.description is not None:
            base["description"] = self.description
        if self.agent is not None:
            base["agent_id"] = self.agent.id
        if self.status_channel_id is not None:
            base["status_channel_id"] = self.status_channel_id
        if self.status_message_id is not None:
            base["status_message_id"] = self.status_message_id
        return base

    def to_first_embed(self):
        embed = discord.Embed(
            title="Ticket Created",
            description=(
                "Our support team has been notified and an agent will assist you soon. We usually respond to support tickets within 24 hours; however, note that responses may be delayed during busy intervals. If you no longer need assistance, please close the ticket by clicking the :lock: **Close Ticket** button below this message."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Subject", value=self.subject)
        embed.add_field(name="Category", value=self.category.label)
        embed.add_field(name="Description", value=self.description, inline=False)
        embed.set_footer(text=self._id)
        return embed

    def to_status_embed(self):
        embed = discord.Embed(title=self._id, color=discord.Color.blurple())
        embed.set_author(name=str(self.user), icon_url=self.user.display_avatar.url)
        embed.add_field(name="Category", value=self.category.label)
        if self.status_channel is not None:
            embed.add_field(name="Status", value=self.status_channel.mention)
        if self.agent is not None:
            embed.color = discord.Color.green()
            embed.add_field(name="Agent", value=self.agent.mention)
        if self.subject is not None:
            embed.add_field(name="Subject", value=self.subject, inline=False)

        footer = [f"User ID • {self.user.id}"]
        if self.closed_at is not None:
            embed.color = None
            footer.append("Ticket Closed")
            embed.timestamp = self.closed_at
        embed.set_footer(text="\n".join(footer))

        return embed

    def to_claim_embed(self):
        return discord.Embed(
            title="Ticket Claimed",
            color=discord.Color.green(),
            description=f"The ticket has been claimed by {self.agent.mention}. You will be assisted shortly.",
        )

    def to_closed_embed(self):
        return discord.Embed(
            title="Ticket Closed",
            color=discord.Color.red(),
            description="The ticket has been closed, and the thread has been archived. Please open another support ticket if you require further assistance.",
        )

    async def edit(
        self,
        *,
        closed_at: Optional[datetime] = MISSING,
        agent: Optional[discord.Member] = MISSING,
        status_channel_id: Optional[int] = MISSING,
    ):
        if self.closed_at is not None:
            return False

        status_message = await self.fetch_status_message()

        if closed_at is not MISSING:
            self.closed_at = closed_at
        if agent is not MISSING:
            self.agent = agent
        if status_channel_id is not MISSING:
            self.status_channel_id = status_channel_id

        await self.update_status_message(status_message)
        await self.bot.mongo.db.ticket.update_one({"_id": self._id}, {"$set": self.to_dict()}, upsert=True)

        return True

    async def fetch_status_message(self):
        if self.status_channel is None or self.status_message_id is None:
            return None
        try:
            return await self.status_channel.fetch_message(self.status_message_id)
        except discord.NotFound:
            return None

    async def update_status_message(self, original=None):
        if original is not None and original.channel.id != self.status_channel_id:
            await original.delete()
            original = None

        if self.status_channel is not None:
            if original is None:
                status_message = await self.status_channel.send(embed=self.to_status_embed(), view=StatusView(self))
                self.status_message_id = status_message.id
            else:
                await original.edit(embed=self.to_status_embed(), view=StatusView(self))

    async def close(self):
        if self.closed_at is not None:
            return False

        await self.edit(closed_at=datetime.now(timezone.utc), status_channel_id=STATUS_CHANNEL_ID_CLOSED)
        with contextlib.suppress(discord.HTTPException):
            await self.thread.send(embed=self.to_closed_embed())
        await self.thread.edit(archived=True, locked=True)

        return True

    async def claim(self, user: discord.Member):
        if self.closed_at is not None:
            return False

        if self.status_channel_id == STATUS_CHANNEL_ID_NEW:
            await self.edit(agent=user, status_channel_id=STATUS_CHANNEL_ID_OPEN)
        else:
            await self.edit(agent=user)

        await self.thread.add_user(user)
        await self.thread.send(embed=self.to_claim_embed())

        return True

    async def add(self, user: discord.Member):
        await self.thread.add_user(user)


class ClaimTicketButton(discord.ui.Button):
    def __init__(self, ticket: Ticket, *, style=discord.ButtonStyle.secondary):
        super().__init__(
            label="Claim",
            emoji="\N{WAVING WHITE FLAG}\ufe0f",
            custom_id=f"persistent:ticket:claim:{ticket._id}",
            style=style,
            disabled=ticket.closed_at is not None,
        )
        self.ticket = ticket

    async def callback(self, interaction: discord.Interaction):
        if any(x.id in constants.TRIAL_MODERATOR_ROLES for x in interaction.user.roles):
            await self.ticket.claim(interaction.user)
            await interaction.response.defer()


class CloseTicketButton(discord.ui.Button):
    def __init__(self, ticket: Ticket, *, style=discord.ButtonStyle.secondary):
        super().__init__(
            label="Close",
            emoji="\N{LOCK}",
            custom_id=f"persistent:ticket:close:{ticket._id}",
            disabled=ticket.closed_at is not None,
            style=style,
        )
        self.ticket = ticket

    async def callback(self, interaction: discord.Interaction):
        if interaction.user == self.ticket.user or any(
            x.id in constants.TRIAL_MODERATOR_ROLES for x in interaction.user.roles
        ):
            await self.ticket.close()
            await interaction.response.defer()


class JumpToTicketButton(discord.ui.Button):
    def __init__(self, ticket: Ticket):
        super().__init__(
            label="Jump to Thread",
            url=f"https://discord.com/channels/930339868503048202/{ticket.thread_id}",
        )
        self.ticket = ticket


class OpenTicketButton(discord.ui.Button):
    def __init__(self, category: HelpDeskCategory):
        super().__init__(label="Open Ticket", style=discord.ButtonStyle.primary)
        self.category = category

    async def callback(self, interaction: discord.Interaction):
        await self.category.open_ticket(interaction)


class OpenTicketModal(discord.ui.Modal):
    subject = discord.ui.TextInput(label="Subject", min_length=5, max_length=200)
    description = discord.ui.TextInput(
        label="Description", style=discord.TextStyle.paragraph, min_length=100, max_length=1024
    )

    def __init__(self, category: HelpDeskCategory):
        super().__init__(title=f"Open Ticket: {category.label}")
        self.category = category
        self.bot = category.bot

    async def on_submit(self, interaction: discord.Interaction):
        guild = self.bot.get_guild(constants.SUPPORT_SERVER_ID)
        channel = guild.get_channel(TICKETS_CHANNEL_ID)

        await self.bot.redis.set(f"ticket:{interaction.user.id}", 1, expire=1200)

        _id = f"{self.category.id.upper()} {await self.bot.mongo.reserve_id(f'ticket_{self.category.id}'):03}"
        thread = await channel.create_thread(
            name=_id,
            auto_archive_duration=10080,
            type=discord.ChannelType.private_thread,
            invitable=False,
            reason=f"Created support ticket for {interaction.user}",
        )
        ticket = Ticket(
            bot=self.bot,
            _id=_id,
            user=interaction.user,
            category=self.category,
            subject=self.subject.value,
            description=self.description.value,
            guild_id=guild.id,
            channel_id=channel.id,
            thread_id=thread.id,
            created_at=discord.utils.snowflake_time(interaction.id),
        )

        await ticket.edit()

        first_view = FirstView(ticket)
        await thread.add_user(interaction.user)
        await thread.send(embed=ticket.to_first_embed(), view=first_view)
        await interaction.response.send_message(f"Opened ticket {thread.mention}.", ephemeral=True)
        await ticket.category.on_open(ticket)


class FirstView(discord.ui.View):
    def __init__(self, ticket: Ticket):
        super().__init__()
        self.stop()
        self.add_item(CloseTicketButton(ticket, style=discord.ButtonStyle.danger))
        self.add_item(ClaimTicketButton(ticket))


class StatusView(discord.ui.View):
    def __init__(self, ticket: Ticket):
        super().__init__()
        self.stop()
        self.add_item(ClaimTicketButton(ticket, style=discord.ButtonStyle.primary))
        self.add_item(CloseTicketButton(ticket, style=discord.ButtonStyle.danger))
        self.add_item(JumpToTicketButton(ticket))


class OpenTicketView(discord.ui.View):
    def __init__(self, category: HelpDeskCategory):
        super().__init__(timeout=600)
        self.add_item(OpenTicketButton(category))


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

    async def on_select(self, interaction: discord.Interaction):
        await self.open_ticket(interaction)

    async def on_open(self, ticket: Ticket):
        pass

    async def respond(self, interaction: discord.Interaction, response: str):
        await interaction.response.send_message(textwrap.dedent(response), ephemeral=True)

    async def respond_then_open_ticket(self, interaction: discord.Interaction, response: str):
        await interaction.response.send_message(textwrap.dedent(response), ephemeral=True, view=OpenTicketView(self))

    async def open_ticket(self, interaction: discord.Interaction):
        cd = await self.bot.redis.pttl(f"ticket:{interaction.user.id}")
        if cd >= 0:
            msg = f"You can open a ticket again in **{time.human_timedelta(timedelta(seconds=cd / 1000))}**."
            return await interaction.response.send_message(msg, ephemeral=True)
        await interaction.response.send_modal(OpenTicketModal(self))


class SetupHelp(HelpDeskCategory):
    id = "setup"
    label = "Setup Help"
    description = "Help with setting up the bot, configuring spawn channels, changing the prefix, permissions, etc."
    emoji = "\N{GEAR}\ufe0f"

    async def on_select(self, interaction: discord.Interaction):
        await self.respond(
            interaction,
            """
            Welcome to Pokétwo! For some common configuration options, use the commands listed below:

            • `p!prefix <new_prefix>` to change prefix.
            • `p!serversilence` to silence level up messages server-wide.
            • `p!location <new_location>` to change location of server (used for day/night calculation).
            • `p!redirect #channel` to redirect to a certain channel
            • `p!redirect #channel1 #channel2 #channel3` etc to redirect to multiple channels.

            Unfortunately, we're not able to handle setup questions in the support server at this time.
            Feel free to check out our Documentation site at <https://docs.poketwo.net/> for common information. However, note that this site is currently an early work in progress.
            If you still have questions, you can ask in our **community server** at discord.gg/poketwo in the #questions-help channel.
            """,
        )


class GeneralQuestions(HelpDeskCategory):
    id = "gen"
    label = "General Pokétwo Questions"
    description = "Questions about command usage, trading, or how the bot works in general."
    emoji = "\N{INFORMATION SOURCE}\ufe0f"

    async def on_select(self, interaction: discord.Interaction):
        await self.respond(
            interaction,
            """
            Hi! Unfortunately, we're not able to handle general questions in the support server at this time.
            Feel free to check out our Documentation site at <https://docs.poketwo.net/> for common information. However, note that this site is currently an early work in progress.
            If you still have questions, you can ask in our **community server** at discord.gg/poketwo in the #questions-help channel.
            """,
        )


class BugReports(HelpDeskCategory):
    id = "bug"
    label = "Bug Reports"
    description = "Report issues that look like bugs or unintended behavior."
    emoji = "\N{BUG}"

    async def on_select(self, interaction: discord.Interaction):
        await self.respond_then_open_ticket(
            interaction,
            """
            Before reporting a bug, please check the #bot-outages and #bot-news channels as well as our GitHub repository at <https://github.com/poketwo/poketwo/issues> to make sure the "bug" is not intended behavior. Note that the bot simply being down does not constitute a bug—bugs are **specific unintended or problematic behaviors**.

            If you have done the above and would still like to report your bug, please press the button below to open a ticket.

            Note that abusing the ticket feature will result in a ban from the support server.
            """,
        )


class Reports(HelpDeskCategory):
    id = "rpt"
    label = "User Reports"
    description = "Report users violating the Pokétwo Terms of Service."
    emoji = "\N{NO ENTRY SIGN}"

    async def on_select(self, interaction: discord.Interaction):
        await self.respond_then_open_ticket(
            interaction,
            """
            This category is for reporting users who you believe have violated the Pokétwo Terms of Service, e.g., through autocatching, crosstrading, or related behaviors. Before making a report, please make sure that the user you are reporting has actually violated a rule. Remember that all reports must have appropriate evidence to back them up.

            If you have checked the above and would still like to file a report, press the button below to open a ticket.

            Note that abusing the ticket feature will result in a ban from the support server.
            """,
        )


class IncenseRefunds(HelpDeskCategory):
    id = "inc"
    label = "Incense Refunds"
    description = "Bot went down in the middle of an incense? Request a refund here."
    emoji = "\N{CANDLE}\ufe0f"

    async def on_select(self, interaction: discord.Interaction):
        await self.respond_then_open_ticket(
            interaction,
            """
            The bot occasionally restarts its shards to stay healthy. If this happens, incenses will briefly pause for 1-2 minutes before automatically resuming. Please wait a few minutes before opening a ticket here in case the incense comes back.

            If you have done so and still need an incense refund, press the button below to open a ticket.

            Note that abusing the ticket feature will result in a ban from the support server.
            """,
        )

    async def on_open(self, ticket: Ticket):
        if ticket.thread is None:
            return
        embed = discord.Embed(
            title="Incense Refund Instructions",
            color=discord.Color.blurple(),
            description=textwrap.dedent(
                """
                Thank you for submitting an incense refund request. We're sorry for the trouble you've encountered with your incense. In order for us to process your request, please submit the following:

                1) A screenshot of you purchasing the incense,
                2) A screenshot of the bot malfunctioning—not sending spawns, not responding to commands, or something else—and
                3) A screenshot of you running `p!stopincense`, and pressing Confirm if it asks (if you have not done so yet, please do this now).

                After you submit these pieces of documentation, someone will come by to refund you shortly. Thank you!
                """
            ),
        )
        await ticket.thread.send(embed=embed)


class StorePurchases(HelpDeskCategory):
    id = "store"
    label = "Store Purchases"
    description = "Payment methods, unreceived items, refunds, disputes, etc."
    emoji = "\N{MONEY WITH WINGS}"

    async def on_select(self, interaction: discord.Interaction):
        await self.respond_then_open_ticket(
            interaction,
            """
            This category is for inquiries related to **real-money transactions** on our online store. Most purchases will be fulfilled immediately; however, in certain cases, such as bot outages, rewards may take a few hours to show up in your account. If you are inquiring about missing rewards, please wait a few hours before opening a ticket.

            If you have checked the above, press the button below to open a ticket.

            Note that abusing the ticket feature will result in a ban from the support server.
            """,
        )


class Punishments(HelpDeskCategory):
    id = "pun"
    label = "Bans & Suspensions"
    description = "Ban lengths, ban reasons, how to appeal, etc."
    emoji = "\N{HAMMER}"

    async def on_select(self, interaction: discord.Interaction):
        await self.respond_then_open_ticket(
            interaction,
            """
            We do not accept appeals through this server. If you would like to appeal a punishment, please do so via our appeals site at https://forms.poketwo.net/.

            If you would still like to open a ticket, please press the button below.

            Note that abusing the ticket feature will result in a ban from the support server.
            """,
        )


class Miscellaneous(HelpDeskCategory):
    id = "misc"
    label = "Other Support Inquiries"
    description = "For questions that do not fit the above categories, choose this option to talk to a staff member."
    emoji = "\N{BLACK QUESTION MARK ORNAMENT}"

    async def on_select(self, interaction: discord.Interaction):
        await self.respond_then_open_ticket(
            interaction,
            """
            Most general questions can be answered in our **community server** at discord.gg/poketwo in the #questions-help channel. If your inquiry is not a special situation relating to your account, please consider asking there first, before opening a ticket.

            If you have already asked in our community server or would like to open a ticket anyway, please press the button below.

            Note that abusing the ticket feature will result in a ban from the support server.
            """,
        )


class HelpDeskSelect(discord.ui.Select):
    def __init__(self, bot):
        self.categories = [cls(bot) for cls in ALL_CATEGORIES.values()]
        super().__init__(
            placeholder="Select Option",
            custom_id="persistent:help_desk_select",
            options=[
                discord.SelectOption(
                    label=category.label,
                    value=category.id,
                    emoji=category.emoji,
                    description=category.description[:100],
                )
                for category in self.categories
            ],
        )
        self.bot = bot

    @cached_property
    def categories_by_id(self):
        return {x.id: x for x in self.categories}

    async def callback(self, interaction: discord.Interaction):
        await self.categories_by_id[self.values[0]].on_select(interaction)


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

    @commands.Cog.listener()
    async def on_thread_update(self, before: discord.Thread, after: discord.Thread):
        if after.archived and not before.archived:
            ticket = await self.fetch_ticket_by_thread(after.id)
            if ticket is None:
                return
            if ticket.closed_at is None:
                await after.edit(archived=False)

    @commands.hybrid_command()
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
        else:
            return

        ticket = await self.fetch_ticket_by_id(ticket_id)
        if ticket is not None:
            await button_cls(ticket).callback(interaction)
        else:
            await interaction.response.send_message("Could not find that ticket!")

    @commands.hybrid_command()
    @checks.support_server_only()
    async def close(self, ctx, *, ticket_thread: discord.Thread = None):
        """Marks a ticket as closed.

        You must be the ticket author, or have the Moderator role, to use this."""

        ticket_thread = ticket_thread or ctx.channel
        ticket = await self.fetch_ticket_by_thread(ticket_thread.id)
        if ticket is None:
            return await ctx.send("Could not find ticket!", ephemeral=True)

        if ctx.author == ticket.user or any(x.id in constants.TRIAL_MODERATOR_ROLES for x in ctx.author.roles):
            result = await ticket.close()
            if ctx.channel != ticket_thread:
                if result:
                    await ctx.send("Successfully closed ticket.")
                else:
                    await ctx.send("Could not close ticket.", ephemeral=True)
        else:
            await ctx.send("You do not have permission to do that!", ephemeral=True)

    @commands.hybrid_command()
    @checks.support_server_only()
    @checks.is_trial_moderator()
    async def claim(self, ctx, *, ticket_thread: discord.Thread = None):
        """Claims a ticket, marking you as the agent.

        You must have the Trial Moderator role to use this."""

        ticket_thread = ticket_thread or ctx.channel
        ticket = await self.fetch_ticket_by_thread(ticket_thread.id)
        if ticket is None:
            return await ctx.send("Could not find ticket!", ephemeral=True)

        result = await ticket.claim(ctx.author)

        if ctx.channel != ticket_thread:
            if result:
                await ctx.send(f"Successfully claimed ticket.")
            else:
                await ctx.send("Could not claim ticket.", ephemeral=True)

    @commands.hybrid_command()
    @checks.support_server_only()
    @checks.is_trial_moderator()
    async def move(self, ctx, ticket_thread: Optional[discord.Thread], status_channel: discord.TextChannel):
        """Moves a ticket to a given status channel.

        You must have the Trial Moderator role to use this."""

        ticket_thread = ticket_thread or ctx.channel
        ticket = await self.fetch_ticket_by_thread(ticket_thread.id)
        if ticket is None:
            return await ctx.send("Could not find ticket!", ephemeral=True)

        if status_channel.category_id != STATUS_CATEGORY_ID or status_channel.id in (
            STATUS_CHANNEL_ID_NEW,
            STATUS_CHANNEL_ID_CLOSED,
        ):
            return await ctx.send("You cannot move the ticket to this channel!", ephemeral=True)

        if await ticket.edit(status_channel_id=status_channel.id):
            await ctx.send(f"Successfully moved ticket to {status_channel.mention}.")
        else:
            await ctx.send("Could not move ticket.", ephemeral=True)

    @commands.hybrid_command()
    @checks.support_server_only()
    @checks.is_trial_moderator()
    async def status(self, ctx, *, ticket_thread: Optional[discord.Thread]):
        """Displays the status for a given ticket.

        You must have the Trial Moderator role to use this."""

        ticket_thread = ticket_thread or ctx.channel
        ticket = await self.fetch_ticket_by_thread(ticket_thread.id)
        if ticket is None:
            return await ctx.send("Could not find ticket!", ephemeral=True)

        await ctx.send(embed=ticket.to_status_embed())

    async def cog_unload(self):
        self.view.stop()


async def setup(bot):
    await bot.add_cog(HelpDesk(bot))
