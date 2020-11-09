import discord


class FakeUser(discord.Object):
    @property
    def avatar_url(self):
        return "https://cdn.discordapp.com/embed/avatars/0.png"

    @property
    def mention(self):
        return "<@{0.id}>".format(self)

    def __str__(self):
        return str(self.id)
