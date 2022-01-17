import discord
from discord.ext import commands

HELP_DESK_TEXT = """
**Welcome to the Pokétwo Help Desk!**

Please select the appropriate option for the type of support you're looking for.

\N{GEAR}\ufe0f **Setup Help**
Help with setting up the bot, configuring spawn channels, changing the prefix, permissions, etc.

\N{INFORMATION SOURCE}\ufe0f **General Pokétwo Questions**
Questions about certain commands, trading, or anything else about how the bot works.

\N{BUG} **Bug Reports**
If you encounter an issue that does not seem to be the intended behavior, you can report it here

\N{NO ENTRY SIGN} **User Reports**
If you see someone breaking the Pokétwo Terms of Service, you can report them here.

\N{BLACK QUESTION MARK ORNAMENT} **Other Support Inquiries**
For anything that does not fit the above categories, choose this to talk to a staff member.
"""


class HelpDeskSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="Setup Help",
                value="setup_help",
                emoji="\N{GEAR}\ufe0f",
            ),
            discord.SelectOption(
                label="General Pokétwo Questions",
                value="general_questions",
                emoji="\N{INFORMATION SOURCE}\ufe0f",
            ),
            discord.SelectOption(
                label="Bug Reports",
                value="bug_reports",
                emoji="\N{BUG}",
            ),
            discord.SelectOption(
                label="User Reports",
                value="reports",
                emoji="\N{NO ENTRY SIGN}",
            ),
            discord.SelectOption(
                label="General Support Inquiries",
                value="miscellaneous",
                emoji="\N{BLACK QUESTION MARK ORNAMENT}",
            ),
        ]
        super().__init__(
            placeholder="Select Option", options=options, custom_id="persistent:help_desk_select"
        )

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        await interaction.response.send_message(f"You selected: {value}", ephemeral=True)


class HelpDeskView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(HelpDeskSelect())


class HelpDesk(commands.Cog):
    """For the help desk on the support server."""

    def __init__(self, bot):
        self.bot = bot
        self.bot.loop.create_task(self.setup_view())

    async def setup_view(self):
        await self.bot.wait_until_ready()
        self.view = HelpDeskView()
        self.bot.add_view(self.view)

    @commands.command()
    async def makedesk(self, ctx):
        await ctx.send(HELP_DESK_TEXT, view=self.view)

    def cog_unload(self):
        self.view.stop()


def setup(bot):
    bot.add_cog(HelpDesk(bot))
