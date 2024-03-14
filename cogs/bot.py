import sys
import traceback
from datetime import timedelta

import discord
from discord.ext import commands

from helpers import time
from helpers.utils import FetchUserConverter


def format_date(date):
    if date is None:
        return "N/A"
    return f"{discord.utils.format_dt(date, 'F')} ({discord.utils.format_dt(date, 'R')})"


class Bot(commands.Cog):
    """For basic bot operation."""

    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.send("This command cannot be used in private messages.", ephemeral=True)
        elif isinstance(error, commands.DisabledCommand):
            await ctx.send("Sorry. This command is disabled and cannot be used.", ephemeral=True)
        elif isinstance(error, commands.BotMissingPermissions):
            missing = [
                "`" + perm.replace("_", " ").replace("guild", "server").title() + "`"
                for perm in error.missing_permissions
            ]
            fmt = "\n".join(missing)
            message = (
                f"💥 Err, I need the following permissions to run this command:\n{fmt}\nPlease fix this and try again."
            )
            try:
                await ctx.send(message, ephemeral=True)
            except discord.Forbidden:
                await ctx.author.send(message)
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send_help(ctx.command)
        elif isinstance(error, commands.CommandOnCooldown):
            await ctx.send(
                f"You're on cooldown! Try again in **{time.human_timedelta(timedelta(seconds=error.retry_after))}**.",
                ephemeral=True,
            )
        elif isinstance(error, commands.BadFlagArgument):
            await ctx.send(error.original, ephemeral=True)
        elif isinstance(error, commands.CheckFailure):
            await ctx.send(error, ephemeral=True)
        elif isinstance(error, commands.UserInputError):
            await ctx.send(error, ephemeral=True)
        elif isinstance(error, commands.CommandNotFound):
            return
        else:
            print(f"Ignoring exception in command {ctx.command}")
            traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)

    @commands.Cog.listener()
    async def on_error(self, event, error):
        if isinstance(error, discord.NotFound):
            return
        else:
            print(f"Ignoring exception in event {event}:")
            traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)

    @commands.hybrid_command()
    async def ping(self, ctx):
        """View the bot's latency."""

        message = await ctx.send("Pong!")
        seconds = (message.created_at - ctx.message.created_at).total_seconds()
        await message.edit(content=f"Pong! **{seconds * 1000:.0f} ms**")

    @commands.hybrid_command(aliases=("whois",))
    async def info(self, ctx, *, user: FetchUserConverter = None):
        """Shows info about a user."""

        user = user or ctx.author
        if ctx.guild is not None and isinstance(user, discord.User):
            user = ctx.guild.get_member(user.id) or user

        embed = discord.Embed()
        embed.set_author(name=str(user))

        embed.add_field(name="ID", value=user.id, inline=False)
        embed.add_field(name="Avatar", value=f"[Link]({user.display_avatar.url})", inline=False)
        embed.add_field(
            name="Joined",
            value=format_date(getattr(user, "joined_at", None)),
            inline=False,
        )
        embed.add_field(
            name="Created",
            value=format_date(user.created_at),
            inline=False,
        )

        if isinstance(user, discord.Member):
            roles = [role.name.replace("@", "@\u200b") for role in user.roles]
            if len(roles) > 10:
                roles = [*roles[:9], f"and {len(roles) - 9} more"]
            embed.add_field(name="Roles", value=", ".join(roles), inline=False)
        else:
            embed.set_footer(text="This user is not in this server.")

        embed.color = user.color
        embed.set_thumbnail(url=user.display_avatar.url)

        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Bot(bot))
