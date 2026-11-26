import math
import re
from decimal import Decimal, localcontext
from fractions import Fraction

import discord
from discord.ext import commands

MAX_LENGTH = 200
MAX_DEPTH = 50
MAX_DIGITS = 1000
MAX_BITS = int(MAX_DIGITS * math.log2(10))
MAX_FACTORIAL = 449
MAX_PLAIN_DIGITS = 30
SIGNIFICANT_DIGITS = 15

SUFFIXES = {"k": 3, "m": 6, "b": 9}
CONSTANTS = {"pi": math.pi, "e": math.e}


def log(value, function):
    if isinstance(value, Fraction):
        return function(value.numerator) - function(value.denominator)
    return function(value)


def sqrt(value):
    if not isinstance(value, Fraction):
        return math.sqrt(value)
    if value < 0:
        raise ValueError
    root = Fraction(math.isqrt(value.numerator), math.isqrt(value.denominator))
    if root * root == value:
        return root
    with localcontext() as context:
        context.prec = SIGNIFICANT_DIGITS + 5
        context.Emax = MAX_DIGITS * 2
        context.Emin = -MAX_DIGITS * 2
        return float(Decimal(value.numerator).sqrt() / Decimal(value.denominator).sqrt())


FUNCTIONS = {
    "sqrt": sqrt,
    "log": lambda x: log(x, math.log10),
    "ln": lambda x: log(x, math.log),
    "sin": lambda x: math.sin(x),
    "cos": lambda x: math.cos(x),
    "tan": lambda x: math.tan(x),
    "floor": math.floor,
    "ceil": math.ceil,
    "round": round,
    "abs": abs,
}
WORDS = sorted([*CONSTANTS, *FUNCTIONS, "mod", "x"], key=len, reverse=True)

TOKEN_RE = re.compile(
    r"\s*(?:"
    r"(?P<number>(?:\d(?:\d|[,_](?=\d))*(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?(?:k|b|m(?!od))?)"
    rf"|(?P<word>{'|'.join(WORDS)})"
    r"|(?P<op>\*\*|//|[-+*/^%!()])"
    r")"
)
NUMBER_RE = re.compile(r"(?P<mantissa>[\d.]+)(?:e(?P<exponent>[+-]?\d+))?(?P<suffix>[kmb])?")

TRANSLATIONS = str.maketrans({"×": "*", "÷": "/", "−": "-", "`": None})

CALC_HELP = f"""Calculate a math expression.

**Operators**
`+` `-` — add, subtract
`*` `x` `×` — multiply
`/` `÷` — divide
`//` — divide and round down
`mod` — remainder (`17 mod 5` → `2`)
`^` `**` — power (`2^10` → `1,024`)
`%` — percent, divides by 100 (`20% x 1m` → `200,000`)
`!` — factorial (`5!` → `120`)
`( )` — brackets, `2(3 + 4)` and `2pi` multiply

**Numbers**
`1,500,000` `1_000` `1.5` `.5`
`250k` `1.5m` `2b` — thousand, million, billion
`1e6` `2.5e-3` — scientific notation

**Constants**
`pi` `e`

**Functions**
`sqrt` — square root
`log` `ln` — log base 10, natural log
`sin` `cos` `tan` — in radians
`floor` `ceil` `round` — round down, up, nearest
`abs` — absolute value
Use them like `sqrt(16)` or `sqrt 16`.

**Order**
Brackets, then `!` `%`, then `^` (right to left), then a `-` sign, then `*` `/` `//` `mod`, then `+` `-`. So `-2^2` → `-4` and `2^3^2` → `512`.

**Results**
Math is exact where it can be: whole parts are always shown in full, decimals get up to {SIGNIFICANT_DIGITS} significant digits. `pi`, `e`, functions and powers like `2^0.5` are approximate, rounded to {SIGNIFICANT_DIGITS} significant digits overall. Results over {MAX_PLAIN_DIGITS} digits show in scientific notation (`1.5e+45`).

**Limits**
Up to {MAX_LENGTH} characters, {MAX_DEPTH} levels of nesting, {MAX_DIGITS:,} digits in any number, and `{MAX_FACTORIAL}!`."""


class CalculationError(Exception):
    pass


def check_size(value):
    if isinstance(value, complex):
        raise CalculationError("Result is not a real number.")
    if isinstance(value, float):
        if math.isnan(value):
            raise CalculationError("Result is not a number.")
        if math.isinf(value):
            raise CalculationError("Result is too big.")
        return value
    value = Fraction(value)
    if value.numerator.bit_length() > MAX_BITS or value.denominator.bit_length() > MAX_BITS:
        raise CalculationError("Result is too big.")
    return value


def parse_number(text):
    match = NUMBER_RE.fullmatch(text.replace(",", "").replace("_", ""))
    mantissa = Fraction(match["mantissa"])
    exponent = int(match["exponent"] or 0) + SUFFIXES.get(match["suffix"], 0)
    if abs(exponent) > MAX_DIGITS:
        raise CalculationError("Number is too big.")
    return check_size(mantissa * Fraction(10) ** exponent)


def tokenize(text):
    tokens = []
    pos = 0
    text = text.rstrip()
    while pos < len(text):
        match = TOKEN_RE.match(text, pos)
        if match is None or match.end() == pos:
            snippet = text[pos:].lstrip()[:10]
            raise CalculationError(f'Invalid expression: unexpected "{snippet}"')
        kind = match.lastgroup
        value = match[kind]
        if kind == "word" and value == "x":
            kind, value = "op", "*"
        tokens.append((kind, value))
        pos = match.end()
    return tokens


def integer_root(value, degree):
    if value < 2:
        return value
    root = 1 << -(-value.bit_length() // degree)
    while True:
        smaller = ((degree - 1) * root + value // root ** (degree - 1)) // degree
        if smaller >= root:
            return root
        root = smaller


def exact_root(value, degree):
    if degree > max(value.numerator.bit_length(), value.denominator.bit_length()):
        return None
    numerator = integer_root(value.numerator, degree)
    denominator = integer_root(value.denominator, degree)
    if numerator**degree == value.numerator and denominator**degree == value.denominator:
        return Fraction(numerator, denominator)
    return None


def power(base, exponent):
    if isinstance(base, Fraction) and isinstance(exponent, Fraction) and base < 0 and exponent.denominator % 2:
        if exponent.denominator != 1:
            result = power(-base, exponent)
            return -result if exponent.numerator % 2 else result
    if isinstance(base, Fraction) and isinstance(exponent, Fraction) and base > 0 and exponent.denominator != 1:
        root = exact_root(base, exponent.denominator)
        if root is not None:
            return power(root, Fraction(exponent.numerator))
    if (
        isinstance(base, Fraction)
        and isinstance(exponent, Fraction)
        and exponent.denominator == 1
        and base not in (-1, 0, 1)
    ):
        bits = max(base.numerator.bit_length(), base.denominator.bit_length())
        if abs(exponent) * (bits - 1) > MAX_BITS:
            raise CalculationError("Result is too big.")
    return base**exponent


def factorial(value):
    if isinstance(value, float) and value.is_integer():
        value = Fraction(int(value))
    if not isinstance(value, Fraction) or value.denominator != 1 or value < 0:
        raise CalculationError("Factorial only works on whole numbers 0 and up.")
    if value > MAX_FACTORIAL:
        raise CalculationError(f"Factorial only goes up to {MAX_FACTORIAL}!.")
    return Fraction(math.factorial(value.numerator))


BINARY_OPS = {
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "*": lambda a, b: a * b,
    "/": lambda a, b: a / b,
    "//": lambda a, b: a // b,
    "mod": lambda a, b: a % b,
    "^": power,
    "**": power,
}


class Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0
        self.depth = 0

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else (None, None)

    def next(self):
        token = self.peek()
        if token[0] is None:
            raise CalculationError("Invalid expression: it ends too early.")
        self.pos += 1
        return token

    def apply(self, op, *args):
        try:
            if op in BINARY_OPS:
                return check_size(BINARY_OPS[op](*args))
            return check_size(op(*args))
        except ZeroDivisionError:
            raise CalculationError("Can't divide by zero.")
        except OverflowError:
            raise CalculationError("Result is too big.")
        except ValueError:
            raise CalculationError("Result is undefined.")

    def parse(self):
        value = self.expression()
        kind, token = self.peek()
        if kind is not None:
            raise CalculationError(f'Invalid expression: unexpected "{token}"')
        return value

    def expression(self):
        value = self.term()
        while self.peek()[1] in ("+", "-"):
            op = self.next()[1]
            value = self.apply(op, value, self.term())
        return value

    def term(self):
        value = self.unary()
        while True:
            kind, token = self.peek()
            if token in ("*", "/", "//", "mod"):
                self.next()
                value = self.apply(token, value, self.unary())
            elif token == "(" or (kind == "word" and token != "mod"):
                value = self.apply("*", value, self.unary())
            else:
                return value

    def enter(self):
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise CalculationError(f"Expression is nested more than {MAX_DEPTH} levels deep.")

    def unary(self):
        self.enter()
        if self.peek()[1] in ("+", "-"):
            op = self.next()[1]
            value = self.unary()
            if op == "-":
                value = self.apply("-", 0, value)
        else:
            value = self.power()
        self.depth -= 1
        return value

    def power(self):
        value = self.postfix()
        if self.peek()[1] in ("^", "**"):
            op = self.next()[1]
            value = self.apply(op, value, self.unary())
        return value

    def postfix(self):
        value = self.primary()
        while self.peek()[1] in ("!", "%"):
            if self.next()[1] == "!":
                value = self.apply(factorial, value)
            else:
                value = self.apply("/", value, Fraction(100))
        return value

    def argument(self):
        if self.peek()[1] not in ("+", "-"):
            return self.primary()
        op = self.next()[1]
        self.enter()
        value = self.argument()
        self.depth -= 1
        return self.apply("-", 0, value) if op == "-" else value

    def primary(self):
        kind, token = self.next()
        if kind == "number":
            return parse_number(token)
        if token in CONSTANTS:
            return CONSTANTS[token]
        if token in FUNCTIONS:
            self.enter()
            value = self.apply(FUNCTIONS[token], self.argument())
            self.depth -= 1
            return value
        if token == "(":
            value = self.expression()
            if self.next()[1] != ")":
                raise CalculationError('Invalid expression: missing ")"')
            return value
        raise CalculationError(f'Invalid expression: unexpected "{token}"')


def scientific(value):
    mantissa, exponent = f"{value:.{SIGNIFICANT_DIGITS - 1}e}".split("e")
    if "." in mantissa:
        mantissa = mantissa.rstrip("0").rstrip(".")
    return f"{mantissa}e{int(exponent):+d}"


def format_result(value):
    with localcontext() as context:
        context.prec = SIGNIFICANT_DIGITS
        context.Emax = MAX_DIGITS * 2
        context.Emin = -MAX_DIGITS * 2
        if isinstance(value, Fraction) and value.denominator == 1:
            if len(str(abs(value.numerator))) <= MAX_PLAIN_DIGITS:
                return f"{value.numerator:,}"
            return scientific(+Decimal(value.numerator))
        if isinstance(value, Fraction):
            whole = abs(value.numerator) // value.denominator
            whole_digits = len(str(whole)) if whole else 0
            if whole_digits <= MAX_PLAIN_DIGITS:
                context.prec = SIGNIFICANT_DIGITS + whole_digits
            number = Decimal(value.numerator) / Decimal(value.denominator)
        else:
            number = +Decimal(value)
        if number.is_zero():
            return "0"
        if -7 < number.adjusted() < MAX_PLAIN_DIGITS:
            text = f"{number:,f}"
            if "." in text:
                text = text.rstrip("0").rstrip(".")
            return text
        return scientific(number)


def calculate(expression):
    expression = expression.lower().translate(TRANSLATIONS).strip()
    if not expression:
        raise CalculationError("Invalid expression: it's empty.")
    if len(expression) > MAX_LENGTH:
        raise CalculationError(f"Expression is longer than {MAX_LENGTH} characters.")
    return format_result(Parser(tokenize(expression)).parse())


class Calculator(commands.Cog):
    """For doing math"""

    def __init__(self, bot):
        self.bot = bot

    @commands.command(aliases=("c", "calculate", "math"), help=CALC_HELP)
    async def calc(self, ctx, *, expression: str):
        try:
            result = f"`{calculate(expression)}`"
        except CalculationError as error:
            result = str(error)
        await ctx.reply(result, mention_author=False, allowed_mentions=discord.AllowedMentions.none())


async def setup(bot):
    await bot.add_cog(Calculator(bot))
