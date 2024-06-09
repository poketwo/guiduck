import os
from urllib.parse import quote_plus

import discord

DATABASE_URI = os.getenv("DATABASE_URI")

if DATABASE_URI is None:
    DATABASE_URI = "mongodb://{}:{}@{}".format(
        quote_plus(os.environ["DATABASE_USERNAME"]),
        quote_plus(os.environ["DATABASE_PASSWORD"]),
        os.environ["DATABASE_HOST"],
    )

POKETWO_DATABASE_URI = os.getenv("POKETWO_DATABASE_URI")

if POKETWO_DATABASE_URI is None:
    POKETWO_DATABASE_URI = "mongodb://{}:{}@{}".format(
        quote_plus(os.environ["POKETWO_DATABASE_USERNAME"]),
        quote_plus(os.environ["POKETWO_DATABASE_PASSWORD"]),
        os.environ["POKETWO_DATABASE_HOST"],
    )

if os.getenv("API_BASE") is not None:
    discord.http.Route.BASE = os.getenv("API_BASE")

PREFIX = os.environ["PREFIX"].split()
DATABASE_NAME = os.environ["DATABASE_NAME"]
POKETWO_DATABASE_NAME = os.environ["POKETWO_DATABASE_NAME"]
BOT_TOKEN = os.environ["BOT_TOKEN"]

REDIS_URI = os.environ["REDIS_URI"]
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")

POKETWO_REDIS_URI = os.environ["POKETWO_REDIS_URI"]
POKETWO_REDIS_PASSWORD = os.getenv("POKETWO_REDIS_PASSWORD")

OUTLINE_BASE_URL = os.getenv("OUTLINE_BASE_URL")
OUTLINE_API_TOKEN = os.getenv("OUTLINE_API_TOKEN")
OUTLINE_COLLECTION_IDS = {
    "development": "2edf0a2f-6e73-43f4-be71-34046af0396f",
    "moderators": "fd94ab37-1937-4c50-954e-577916bf0103",
    "moderator wiki": "9b748311-7743-4980-b3ec-4950465be763",
    "management": "845400ae-75bf-44a8-b6ae-5c9be551f30c"
}