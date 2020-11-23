from datetime import timedelta

from discord.ext import commands
from durations_nlp import Duration

PERIODS = (
    ("year", "y", 60 * 60 * 24 * 365),
    ("month", "M", 60 * 60 * 24 * 30),
    ("day", "d", 60 * 60 * 24),
    ("hour", "h", 60 * 60),
    ("minute", "m", 60),
    ("second", "s", 1),
)


class TimeDelta(commands.Converter):
    async def convert(self, ctx, arg):
        seconds = Duration(arg).to_seconds()
        if seconds <= 0:
            raise commands.BadArgument("Invalid duration.")
        return timedelta(seconds=seconds)


def strfdelta(duration, long=False, max_len=None):
    seconds = int(duration.total_seconds())
    strings = []
    for period_name, period_short, period_seconds in PERIODS:
        if seconds >= period_seconds:
            period_value, seconds = divmod(seconds, period_seconds)
            if long:
                has_s = "s" if period_value > 1 else ""
                strings.append(f"{period_value} {period_name}{has_s}")
            else:
                strings.append(f"{period_value}{period_short}")
        if max_len is not None and len(strings) >= max_len:
            break

    return " ".join(strings)
