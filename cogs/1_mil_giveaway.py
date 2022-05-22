from datetime import datetime, timedelta, timezone

import discord
import pymongo.errors
from discord.ext import commands, tasks


class EnterGiveawayView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Enter Giveaway", style=discord.ButtonStyle.primary, custom_id="persistent:1_mil_giveaway")
    async def enter_giveaway(self, interaction, _button):
        try:
            await self.bot.mongo.db.one_mil_giveaway_entry.insert_one({"_id": interaction.user.id})
        except pymongo.errors.DuplicateKeyError:
            await interaction.response.send_message("You have already entered the giveaway!", ephemeral=True)
        else:
            await interaction.response.send_message("You have entered the giveaway!", ephemeral=True)


class OneMilGiveaway(commands.Cog):
    """For adding roles utility."""

    def __init__(self, bot):
        self.bot = bot
        self.view = EnterGiveawayView(self.bot)
        self.message = None
        self.bot.add_view(self.view)
        self.edit_entrants.start()

    @commands.command()
    @commands.is_owner()
    async def send_giveaway_message(self, ctx):
        self.message = await ctx.send("Click the button below to enter the giveaway!", view=self.view)
        self.edit_entrants.cancel()
        self.edit_entrants.start()

    @tasks.loop(seconds=30)
    async def edit_entrants(self):
        if self.message is None:
            channel = self.bot.get_guild(716390832034414685).get_channel(717883936314753045)
            message = await channel.fetch_message(channel.last_message_id)
            if message.author != self.bot.user:
                return
            self.message = message

        if self.message is None:
            return

        number = await self.bot.mongo.db.one_mil_giveaway_entry.count_documents({})
        await self.message.edit(
            content=(
                "Click the button below to enter the giveaway!\n"
                f"Current # entrants: {number:,}\n"
                f"Next update {discord.utils.format_dt(self.edit_entrants.next_iteration, 'R')}"
            )
        )

    @edit_entrants.before_loop
    async def before_edit_entrants(self):
        await self.bot.wait_until_ready()

    async def cog_unload(self):
        self.edit_entrants.cancel()


async def setup(bot):
    await bot.add_cog(OneMilGiveaway(bot))
