from discord.ext import commands

IV_FLAGS = "iv_hp", "iv_atk", "iv_defn", "iv_satk", "iv_sdef", "iv_spd"


def format_pokemon_details(bot: commands.Bot, pokemon: dict):
    species = bot.data.species_by_number(pokemon["species_id"]).name
    shiny = "\N{SPARKLES} " if pokemon["shiny"] else ""
    yield f"\N{EN DASH} Level {pokemon['level']} {shiny}{species}"

    iv = pokemon["iv_total"] / 186
    iv_distr = " / ".join(str(pokemon[x]) for x in IV_FLAGS)
    yield f"\N{IDEOGRAPHIC SPACE}\N{EN DASH} IV: {iv_distr} ({iv:.2%})"

    if _id := pokemon.get("_id"):
        yield f"\N{IDEOGRAPHIC SPACE}\N{EN DASH} ID: {_id}"


def format_pokemon(bot: commands.Bot, pokemon: dict):
    species = bot.data.species_by_number(pokemon["species_id"]).name
    shiny = "\N{SPARKLES} " if pokemon["shiny"] else ""
    iv = pokemon["iv_total"] / 186
    return f"{shiny}{species} ({iv:.2%})"
