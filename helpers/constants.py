DEVELOPER_ROLE = 1120600250474827856
BOT_ADMIN_ROLE = 1219501453240959006
SERVER_ADMIN_ROLE = 1219500880534179892

ADMIN_ROLES = (718006431231508481,)
COMMUNITY_MANAGER_ROLES = (*ADMIN_ROLES, 930346842586218607)
BOT_ADMIN_ROLES = (*COMMUNITY_MANAGER_ROLES, BOT_ADMIN_ROLE)
SERVER_ADMIN_ROLES = (*COMMUNITY_MANAGER_ROLES, SERVER_ADMIN_ROLE)
MODERATOR_ROLES = (*COMMUNITY_MANAGER_ROLES, 724879492622843944, 930346843521556540)
TRIAL_MODERATOR_ROLES = (*MODERATOR_ROLES, 813433839471820810, 930346845547409439)

COMMUNITY_SERVER_ID = 716390832034414685
SUPPORT_SERVER_ID = 930339868503048202

EMBED_FIELD_CHAR_LIMIT = 1024
CONTENT_CHAR_LIMIT = 2000

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
