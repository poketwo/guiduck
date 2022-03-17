import itertools

import discord
from discord.ext import commands
from helpers import pagination


class CustomHelpCommand(commands.HelpCommand):
    def __init__(self):
        super().__init__(command_attrs={"help": "Show help about the bot, a command, or a category."})

    async def on_help_command_error(self, ctx, error):
        if isinstance(error, commands.CommandInvokeError):
            await ctx.send(str(error.original))

    def make_page_embed(self, commands, title="Help", description=None):
        embed = discord.Embed(color=discord.Color.blurple())
        embed.title = title
        embed.description = description
        embed.set_footer(text=f'Use "{self.context.clean_prefix}help command" for more info on a command.')

        for command in commands:
            signature = self.context.clean_prefix + command.qualified_name + " " + command.signature
            help = command.help or "No help found..."

            embed.add_field(
                name=signature,
                value=help.splitlines()[0],
                inline=False,
            )

        return embed

    def make_default_embed(self, cogs, title="Command Categories", description=None):
        embed = discord.Embed(color=discord.Color.blurple())
        embed.title = title
        embed.description = description

        counter = 0
        for cog in cogs:
            cog, description, command_list = cog
            description = f"{description or 'No Description'} \n {''.join([f'`{command.qualified_name}` ' for command in command_list])}"
            embed.add_field(name=cog.qualified_name, value=description, inline=False)
            counter += 1

        return embed

    async def send_bot_help(self, mapping):
        ctx = self.context
        bot = ctx.bot

        def get_category(command):
            cog = command.cog
            return cog.qualified_name if cog is not None else "\u200bNo Category"

        pages = []
        total = 0

        filtered = await self.filter_commands(bot.commands, sort=True, key=get_category)

        for cog_name, commands in itertools.groupby(filtered, key=get_category):
            commands = sorted(commands, key=lambda c: c.name)

            if len(commands) == 0:
                continue

            total += len(commands)
            cog = bot.get_cog(cog_name)
            description = (cog and cog.description) if (cog and cog.description) is not None else None
            pages.append((cog, description, commands))

        async def get_page(pidx):
            cogs = pages[min(len(pages) - 1, pidx * 6) : min(len(pages) - 1, pidx * 6 + 6)]

            embed = self.make_default_embed(
                cogs,
                title=f"Command Categories (Page {pidx+1}/{len(pages)//6+1})",
                description=(
                    f"Use `{self.context.clean_prefix}help <command>` for more info on a command.\n"
                    f"Use `{self.context.clean_prefix}help <category>` for more info on a category."
                ),
            )

            # embed.set_author(name=f"Page {pidx + 1}/{len(pages)} ({total} commands)")

            return embed

        paginator = pagination.Paginator(get_page, len(pages) // 6 + 1)
        await paginator.send(ctx)

    async def send_cog_help(self, cog):
        ctx = self.context

        filtered = await self.filter_commands(cog.get_commands(), sort=True)

        embed = self.make_page_embed(
            filtered,
            title=(cog and cog.qualified_name or "Other") + " Commands",
            description=None if cog is None else cog.description,
        )

        await ctx.send(embed=embed)

    async def send_group_help(self, group):
        ctx = self.context

        subcommands = group.commands
        if len(subcommands) == 0:
            return await self.send_command_help(group)

        filtered = await self.filter_commands(subcommands, sort=True)

        embed = self.make_page_embed(
            filtered,
            title=self.get_command_signature(group),
            description=f"{group.description}\n\n{group.help}"
            if group.description
            else group.help or "No help found...",
        )

        await ctx.send(embed=embed)

    async def send_command_help(self, command):
        embed = discord.Embed(color=discord.Color.blurple())
        embed.title = self.get_command_signature(command)

        if command.description:
            embed.description = f"{command.description}\n\n{command.help}"
        else:
            embed.description = command.help or "No help found..."

        await self.context.send(embed=embed)


async def setup(bot):
    bot.old_help_command = bot.help_command
    bot.help_command = CustomHelpCommand()


def teardown(bot):
    bot.help_command = bot.old_help_command
