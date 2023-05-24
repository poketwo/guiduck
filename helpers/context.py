import typing

import discord
from discord.ext import commands


class ConfirmationView(discord.ui.View):
    def __init__(self, ctx, *, timeout, delete_after) -> None:
        super().__init__(timeout=timeout)
        self.result = None
        self.ctx = ctx
        self.message = None
        self.delete_after = delete_after

    async def interaction_check(self, interaction):
        if interaction.user.id not in {
            self.ctx.bot.owner_id,
            self.ctx.author.id,
            *self.ctx.bot.owner_ids,
        }:
            await interaction.response.send_message("You can't use this!", ephemeral=True)
            return False
        return True

    async def set_result(self, result):
        self.result = result
        self.stop()
        if self.message is None:
            return
        if self.delete_after:
            await self.message.delete()
        else:
            await self.message.edit(view=None)

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.green)
    async def confirm(self, interaction, button):
        await interaction.response.defer()
        await self.set_result(True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction, button):
        await interaction.response.defer()
        await self.set_result(False)

    async def on_timeout(self):
        if self.message:
            await self.message.delete()


class GuiduckContext(commands.Context):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    async def confirm(self, content=None, *, timeout=40, delete_after=False, cls=ConfirmationView, **kwargs):
        view = cls(self, timeout=timeout, delete_after=delete_after)
        view.message = await self.send(content, view=view, **kwargs)
        await view.wait()
        return view.result
