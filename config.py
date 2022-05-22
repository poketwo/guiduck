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
