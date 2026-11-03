from discord.ext import commands

IV_FLAGS = "iv_hp", "iv_atk", "iv_defn", "iv_satk", "iv_sdef", "iv_spd"

# Mirrors constants.FILTER_BY_DUPLICATES in the Pokétwo bot.
DUPLICATE_TIERS = {"triple": 3, "quadruple": 4, "pentuple": 5, "hextuple": 6}


def computed_fields(ivs: list[int]) -> dict[str, int | list[int]]:
    """The ``computed`` document Pokétwo stores on every pokémon (mirrors ``cogs.mongo.computed_fields`` there).

    ``iv_total`` plus each duplicate tier holding the IV value repeated at least that many times,
    or a list when two values qualify; tiers with no repeated value are left out.
    """
    computed: dict[str, int | list[int]] = {"iv_total": sum(ivs)}
    for tier, amount in DUPLICATE_TIERS.items():
        values = sorted({value for value in ivs if ivs.count(value) >= amount})
        if len(values) == 1:
            computed[tier] = values[0]
        elif values:
            computed[tier] = values
    return computed


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
