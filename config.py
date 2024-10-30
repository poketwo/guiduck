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
    "admins": "845400ae-75bf-44a8-b6ae-5c9be551f30c",
    "management": "09f803bd-732a-4f4f-8dcf-1d1a4d6e36f0",
    "bot managers": "a7bfd3b6-85a2-4c85-bd37-23691fa8c499",
    "server managers": "ba5f0149-dfe5-4922-8b14-be8b33a55577",
    "development": "2edf0a2f-6e73-43f4-be71-34046af0396f",
    "moderators": "fd94ab37-1937-4c50-954e-577916bf0103",
    "moderator wiki": "9b748311-7743-4980-b3ec-4950465be763",
    "in-progress": "e724cdc4-32a0-4348-af1e-dad2caa50dd2",
    "information archive": "ce16b7ad-c843-4f10-8d07-817364be3f69",
}