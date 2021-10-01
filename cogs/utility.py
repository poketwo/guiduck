import math

import discord
from discord.ext import commands

class Utility(commands.Cog):
    """Useful tools"""

    def __init__(self, bot):
        self.bot = bot
    
    @commands.command()
    async def shinyrate(self, ctx, streak=1):
        """Check the shinyrate for a specific shiny hunt streak"""

        embed = discord.Embed(
            color= discord.Color.blurple(), 
            title = f"Shiny Rate for {streak} shiny hunt streak"
        )

        embed.add_field(
            name = "Without shiny charm", 
            value = f"1 in {4096/(1+math.log(1+streak/30)): .3f}", 
            inline = False
        )
        embed.add_field(
            name = "With shiny charm", 
            value = f"1 in {3276.8/(1+math.log(1+streak/30)): .3f}",
            inline = False
        )
        await ctx.send(embed = embed)

    @commands.command()
    async def links()
        """View some useful Pokétwo links"""

        embed = discord.Embed(
            color= discord.Color.blurple(), 
            title = f"Links"
        )

        embed.add_field(
            name = "Website", 
            value = f"https://poketwo.net/", 
            inline = False
        )

        embed.add_field(
            name = "Appeals/Application Forms", 
            value = f"https://forms.poketwo.net/", 
            inline = False
        )

def setup(bot):
    bot.add_cog(Utility(bot))
