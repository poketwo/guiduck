import textwrap

import discord


COMMUNITY_MANAGER_ROLES = (718006431231508481, 930346842586218607)
MODERATOR_ROLES = (*COMMUNITY_MANAGER_ROLES, 724879492622843944, 930346843521556540)
TRIAL_MODERATOR_ROLES = (*MODERATOR_ROLES, 813433839471820810, 930346845547409439)

COMMUNITY_SERVER_ID = 716390832034414685
SUPPORT_SERVER_ID = 930339868503048202

EMBED_FIELD_CHAR_LIMIT = 1024

POKEMON_NATURES = [
    "Adamant",
    "Bashful",
    "Bold",
    "Brave",
    "Calm",
    "Careful",
    "Docile",
    "Gentle",
    "Hardy",
    "Hasty",
    "Impish",
    "Jolly",
    "Lax",
    "Lonely",
    "Mild",
    "Modest",
    "Naive",
    "Naughty",
    "Quiet",
    "Quirky",
    "Rash",
    "Relaxed",
    "Sassy",
    "Serious",
    "Timid",
]


WHITE_CROSS_MARK_EMOJI = "<:white_cross_mark:1193650425166045224>"

EMERGENCY_RULES_EMBED = (
    discord.Embed(
        color=discord.Color.blurple(),
        title="Use Cases & Abuse",
        description=(
            "Abuse of this alert system is **strictly prohibited** and **will** result in repercussions if used maliciously."
            " Below are some examples to help understand when and when not to use it. This is not exhaustive."
        ),
    )
    .add_field(
        name="✅ Acceptable Cases",
        value=textwrap.dedent(
            f"""
        - Sending NSFW/disturbing content in our server(s)/DMs
        - Advertising Crosstrading/Distribution of automated scripts in our server(s) that violate our ToS
        - Malicious/excessive spam in our server(s)
        - Advertising links to malicious/scam websites in our server(s)/DMs
        - Extreme Toxicity/Harassment/Trolling
        - Actively violating any other rule to an excessive extent
        """,
        ),
        inline=False,
    )
    .add_field(
        name=f"{WHITE_CROSS_MARK_EMOJI} Unacceptable Cases",
        value=textwrap.dedent(
            f"""
        - Suspected autocatching in our server(s)
        - Server advertisement
        - Bot outages/bugs/glitches — Please use #bug-reports or ping a Developer in case of emergency
        - Asking staff to check appeals/applications
        """,
        ),
        inline=False,
    )
    .set_footer(
        text="Please use `?report` in cases that violate our rules but are unacceptable for an emergency alert."
    )
)
