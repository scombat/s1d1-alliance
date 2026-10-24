"""Validated patterns for personal data and secrets, the complement of the model.

`find_patterns(text)` returns non-overlapping spans `(start, end, marker)`, the union of all findings. Every numeric
pattern is checked (Luhn for cards, ISO 13616 mod 97 for IBANs, the key of the French NIR, the shape of a phone
number) so that dates, prices, versions and counts are left alone. No I/O, no logging.

The patterns run on a normalised copy of the text in which some characters are replaced one for one (Arabic-Indic
and full-width digits, non-breaking spaces, Unicode dashes): offsets are the same in both, so a span found in the
copy is a span of the original.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import math
import re
from collections import Counter
from collections.abc import Callable, Iterator
from itertools import pairwise

Span = tuple[int, int, str]

PERSON = "[PERSON]"
EMAIL = "[EMAIL]"
PHONE = "[PHONE]"
ADDRESS = "[ADDRESS]"
ID = "[ID]"
CARD = "[CARD]"
IBAN = "[IBAN]"
SECRET = "[SECRET]"
IP = "[IP]"
URL_CREDENTIALS = "[URL_CREDENTIALS]"

# --- normalisation (one code point for one code point) ------------------------------------------------------

_ONE_FOR_ONE: dict[int, str] = {}
for _zero in (0x0660, 0x06F0, 0x0966, 0xFF10):  # Arabic-Indic, extended Arabic-Indic (Persian), Devanagari, full width
    for _i in range(10):
        _ONE_FOR_ONE[_zero + _i] = str(_i)
for _space in (0x00A0, 0x2007, 0x2009, 0x202F, 0x200E, 0x200F, 0x2066, 0x2067, 0x2068, 0x2069, 0x3000):
    _ONE_FOR_ONE[_space] = " "  # no-break and thin spaces, bidi marks (common around numbers in Arabic text)
for _dash in (0x2010, 0x2011, 0x2012, 0x2013, 0xFF0D):
    _ONE_FOR_ONE[_dash] = "-"
_ONE_FOR_ONE.update({0xFF0B: "+", 0xFF08: "(", 0xFF09: ")", 0xFF20: "@", 0xFF0E: "."})
_NORMALISE = str.maketrans(_ONE_FOR_ONE)


def normalise(text: str) -> str:
    """The text with the characters above replaced; same length, same offsets."""
    return text.translate(_NORMALISE)


# --- checksums --------------------------------------------------------------------------------------------------


def luhn_ok(digits: str) -> bool:
    """The Luhn check of a card number (digits only, separators removed)."""
    if not digits.isdigit():
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


# Lengths of the IBAN registry (SWIFT), by country. A candidate from another country is not an IBAN.
IBAN_LENGTHS = {
    "AD": 24,
    "AE": 23,
    "AL": 28,
    "AT": 20,
    "AZ": 28,
    "BA": 20,
    "BE": 16,
    "BG": 22,
    "BH": 22,
    "BI": 27,
    "BR": 29,
    "BY": 28,
    "CH": 21,
    "CR": 22,
    "CY": 28,
    "CZ": 24,
    "DE": 22,
    "DJ": 27,
    "DK": 18,
    "DO": 28,
    "EE": 20,
    "EG": 29,
    "ES": 24,
    "FI": 18,
    "FK": 18,
    "FO": 18,
    "FR": 27,
    "GB": 22,
    "GE": 22,
    "GI": 23,
    "GL": 18,
    "GR": 27,
    "GT": 28,
    "HN": 28,
    "HR": 21,
    "HU": 28,
    "IE": 22,
    "IL": 23,
    "IQ": 23,
    "IS": 26,
    "IT": 27,
    "JO": 30,
    "KW": 30,
    "KZ": 20,
    "LB": 28,
    "LC": 32,
    "LI": 21,
    "LT": 20,
    "LU": 20,
    "LV": 21,
    "LY": 25,
    "MC": 27,
    "MD": 24,
    "ME": 22,
    "MK": 19,
    "MN": 20,
    "MR": 27,
    "MT": 31,
    "MU": 30,
    "NI": 28,
    "NL": 18,
    "NO": 15,
    "OM": 23,
    "PK": 24,
    "PL": 28,
    "PS": 29,
    "PT": 25,
    "QA": 29,
    "RO": 24,
    "RS": 22,
    "RU": 33,
    "SA": 24,
    "SC": 31,
    "SD": 18,
    "SE": 24,
    "SI": 19,
    "SK": 24,
    "SM": 27,
    "SO": 23,
    "ST": 25,
    "SV": 28,
    "TL": 23,
    "TN": 24,
    "TR": 26,
    "UA": 29,
    "VA": 22,
    "VG": 24,
    "XK": 20,
    "YE": 30,
}


def iban_ok(value: str) -> bool:
    """ISO 13616: a registered country, its length, and the mod 97 check equal to 1."""
    compact = value.replace(" ", "").upper()
    if not re.fullmatch(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}", compact):
        return False
    if IBAN_LENGTHS.get(compact[:2]) != len(compact):
        return False
    rearranged = compact[4:] + compact[:4]
    return int("".join(str(int(ch, 36)) for ch in rearranged)) % 97 == 1


def shannon_entropy(token: str) -> float:
    """Bits per character of the token's own character distribution."""
    if not token:
        return 0.0
    n = len(token)
    return -sum(c / n * math.log2(c / n) for c in Counter(token).values())


# --- secrets ----------------------------------------------------------------------------------------------------

_PREFIXED = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"sk-[A-Za-z0-9_\-]{20,}"  # OpenAI, Anthropic and others (sk-proj-, sk-ant-)
    r"|(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}"  # Stripe secret and restricted keys
    r"|gh[pousr]_[A-Za-z0-9]{30,}"  # GitHub tokens
    r"|github_pat_[A-Za-z0-9_]{30,}"  # GitHub fine-grained tokens
    r"|(?:AKIA|ASIA)[0-9A-Z]{16}(?![0-9A-Za-z])"  # AWS access key ids
    r"|xox[abprs]-[A-Za-z0-9\-]{10,}"  # Slack tokens
    r"|hf_[A-Za-z0-9]{30,}"  # Hugging Face tokens
    r"|AIza[0-9A-Za-z_\-]{35}"  # Google API keys
    r"|glpat-[A-Za-z0-9_\-]{20,}"  # GitLab tokens
    r"|npm_[A-Za-z0-9]{36}"  # npm tokens
    r"|eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"  # JSON Web Tokens
    r")"
)
_PEM_BEGIN = re.compile(r"-----BEGIN ([A-Z0-9 ]{1,64})-----")
_AUTH_HEADER = re.compile(r"(?i)\b(bearer|basic)[ \t]+([A-Za-z0-9._~+/\-]{12,}=*)")

# The password family: a value given to one of these is a secret even when it is letters only.
_PASSWORD_KEYWORDS = (
    r"pass(?:word|wd|phrase)?|pwd|mot de passe|mdp|passwort|kennwort|contrase(?:ñ|n)a|senha|пароль|密码|口令"
    r"|パスワード|كلمة (?:المرور|السر)"
)
_OTHER_KEYWORDS = (
    r"secret(?:[_\- ]?key)?|api[_\- ]?key|access[_\- ]?(?:key|token)|auth[_\- ]?token|refresh[_\- ]?token"
    r"|client[_\- ]?secret|private[_\- ]?key|token"
)
_KEYWORDS = rf"(?:(?P<pw>{_PASSWORD_KEYWORDS})|{_OTHER_KEYWORDS})"
_KW_QUOTED = re.compile(
    r"(?i)(?<![A-Za-z])" + _KEYWORDS + r"[\"']?[ \t]*[:=][ \t]*(?P<q>[\"'])(?P<v>[^\"'\r\n]{4,256})(?P=q)"
)
_KW_BARE = re.compile(
    r"(?i)(?<![A-Za-z])" + _KEYWORDS + r"[ \t]*(?:[:=：]|\bis\b|\best\b|\bist\b|\bes\b|是|为)[ \t]*"
    r"(?P<v>(?:[\x21\x23-\x26\x28-\x3b\x3d\x3f-\x7e]|[^\W\x00-\x7f]){4,128})"  # printable ASCII or any letter
)
_PLACEHOLDER = re.compile(
    r"(?i)^(?:[<{$%\[*]|x{3,}|\.{3}|your[_\-]|my[_\-]|changeme$|example|placeholder"
    r"|(?:your|my)[_\-]?(?:pass(?:word)?|secret|token|key|api[_\-]?key)$"
    r"|(?:password|passwd|secret|token|api[_\-]?key|none|null|undefined|string|str|true|false|test)$)"
)
_PATH = re.compile(r"^(?:\.{0,2}/|~/|[A-Za-z]:\\)")
_CODE_REFERENCE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+(?:[(\[].*)?$|^[A-Za-z_][\w.]*[(\[]")

ENTROPY_MIN = 4.0  # bits per character
TOKEN_MIN = 32  # characters
_TOKEN_CHARS = "A-Za-z0-9+/=_\\-"
_TOKEN = re.compile(rf"(?<![{_TOKEN_CHARS}])[{_TOKEN_CHARS}]{{{TOKEN_MIN},}}(?![{_TOKEN_CHARS}])")
_CHUNK = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])")
_HEX_ONLY = re.compile(r"[0-9A-Fa-f\-]+")
_HEX_RUN = re.compile(r"(?:0x)?[0-9A-Fa-f]{32,}")
_UUID_FULL = re.compile(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}")

_VOWELS = frozenset("aeiouyAEIOUY")


def _wordlike(token: str) -> bool:
    """True when most letters belong to word-sized, pronounceable chunks (camelCase, snake_case, chemical names).

    A chunk counts as a word when it has 4 letters or more and a fifth of them are vowels (random strings have long
    runs of consonants), and the token is wordlike when 60 % of its letters are in such chunks. Measured on 3 000
    random tokens of 32 to 64 characters, 0.2 % pass as words.
    """
    letters = sum(ch.isalpha() for ch in token)
    if letters == 0:
        return True
    in_words = sum(
        len(chunk)
        for chunk in _CHUNK.findall(token)
        if len(chunk) >= 4 and sum(ch in _VOWELS for ch in chunk) * 5 >= len(chunk)
    )
    return in_words * 5 >= letters * 3


def _decoded_text(token: str) -> str | None:
    """The token decoded from base64 (standard or URL-safe) when that gives readable text, else None."""
    body = token.rstrip("=")
    if len(body) % 4 == 1:
        return None
    padded = body + "=" * (-len(body) % 4)
    for altchars in (None, b"-_"):
        try:
            raw = base64.b64decode(padded, altchars=altchars, validate=True)
            text = raw.decode("utf-8")
        except (binascii.Error, ValueError, UnicodeDecodeError):
            continue
        if text and any(ch.isspace() for ch in text) and all(ch.isprintable() or ch in "\t\r\n" for ch in text):
            return text
    return None


def _is_random_token(token: str, depth: int) -> bool:
    """The entropy rule on a whole token: 32+ characters, letters and digits, >= 4.0 bits, not an identifier."""
    if not (any(ch.isalpha() for ch in token) and any(ch.isdigit() for ch in token)):
        return False
    if _HEX_RUN.fullmatch(token):
        return True  # 32+ hex digits mixing letters and digits: a key or a hash, either way not text
    if _UUID_FULL.fullmatch(token) or _HEX_ONLY.fullmatch(token):
        return False  # identifiers (handled as [ID]) and dashed hex
    if shannon_entropy(token) < ENTROPY_MIN or _wordlike(token):
        return False
    decoded = _decoded_text(token)
    if decoded is not None:
        # base64 of plain text is kept, unless the text itself carries something the patterns find
        return depth > 0 or bool(_find(normalise(decoded), depth + 1))
    return True


def _is_random_part(part: str) -> bool:
    """A part of a path or a query value that looks random: 20+ characters, mixed case, mostly letters, a digit."""
    alnum = [ch for ch in part if ch.isalnum()]
    letters = sum(ch.isalpha() for ch in alnum)
    digits = len(alnum) - letters
    if _HEX_RUN.fullmatch(part) and letters and digits:
        return True
    return (
        len(part) >= 20
        and any(ch.isupper() for ch in part)
        and any(ch.islower() for ch in part)
        and digits >= 1
        and letters >= len(alnum) / 2
        and not _HEX_ONLY.fullmatch(part)
        and shannon_entropy(part) >= 3.5
        and not _wordlike(part)
    )


_PART_SPLIT = re.compile(r"/|=(?!=*$)")  # path separators, and "=" except the base64 padding at the end


def _random_spans(token: str, depth: int, after_dot: bool = False) -> list[tuple[int, int]]:
    """The spans (relative to the token) to replace by [SECRET]; `after_dot` when the token follows a "." (a URL)."""
    cuts = [m.start() for m in _PART_SPLIT.finditer(token)]
    if not cuts:
        return [(0, len(token))] if _is_random_token(token, depth) else []
    decoded = _decoded_text(token)
    if decoded is not None:  # base64 of text: kept, unless the text carries something the patterns find
        return [(0, len(token))] if depth > 0 or _find(normalise(decoded), depth + 1) else []
    bounds = [-1, *cuts, len(token)]
    parts = [(bounds[k] + 1, bounds[k + 1]) for k in range(len(bounds) - 1)]
    words = any(token[s:e].isalpha() and token[s:e].islower() and e - s >= 3 for s, e in parts)
    if not ("=" in token.rstrip("=") or words or after_dot or token.startswith("/")):
        # no sign of a URL: base64 that happens to contain "/", judged whole
        return [(0, len(token))] if _is_random_token(token, depth) else []
    # a URL path or a query string (/file/d/<id>/view, ?sig=<value>): only the random parts go
    return [(s, e) for s, e in parts if e > s and _is_random_part(token[s:e])]


def _secrets_prefixed(t: str, depth: int) -> Iterator[Span]:
    for m in _PREFIXED.finditer(t):
        yield m.start(), m.end(), SECRET
    for m in _AUTH_HEADER.finditer(t):
        value = m.group(2)
        mixed = any(c.isdigit() for c in value) and any(c.isalpha() for c in value)
        if m.group(1).lower() == "basic":
            # "Basic <base64 of user:password>", not "basic psychological needs"
            try:
                credentials = base64.b64decode(value + "=" * (-len(value) % 4), validate=True)
            except (binascii.Error, ValueError):
                credentials = b""
            if b":" in credentials or mixed or value.endswith("="):
                yield m.start(2), m.end(2), SECRET
        elif mixed:
            yield m.start(2), m.end(2), SECRET
    for m in _PEM_BEGIN.finditer(t):
        # through the END line, whatever the length of the lines in between; without one (a truncated paste), to the
        # end of the text: what follows a key header is the key
        end_marker = f"-----END {m.group(1)}-----"
        found = t.find(end_marker, m.end())
        yield m.start(), (found + len(end_marker) if found >= 0 else len(t)), SECRET
        if found < 0:
            break


def _bare_value_ok(value: str, password: bool) -> bool:
    if _PLACEHOLDER.match(value) or _CODE_REFERENCE.match(value) or _PATH.match(value):
        return False  # a placeholder, a code reference (settings.API_KEY) or a file path (private key: ~/.ssh/id_rsa)
    if password and len(value) >= 4:
        return True  # "mot de passe : soleil", "password is hunter": letters only is still a password
    has_letter = any(c.isalpha() for c in value)
    has_digit = any(c.isdigit() for c in value)
    has_symbol = any(c in "!@#$%^&*+?~|" for c in value[1:])
    return len(value) >= 6 and ((has_letter and has_digit) or has_symbol)


def _secrets_by_keyword(t: str, depth: int) -> Iterator[Span]:
    for m in _KW_QUOTED.finditer(t):
        value = m.group("v")
        if not _PLACEHOLDER.match(value) and value.strip():
            yield m.start("v"), m.end("v"), SECRET
    for m in _KW_BARE.finditer(t):
        value = m.group("v").rstrip(".,;:)]}")
        # "pass" alone is too common a word ("pass: the next level") to vouch for a letters-only value
        password = m.group("pw") is not None and m.group("pw").lower() != "pass"
        if _bare_value_ok(value, password):
            yield m.start("v"), m.start("v") + len(value), SECRET


def _secrets_by_entropy(t: str, depth: int) -> Iterator[Span]:
    for m in _TOKEN.finditer(t):
        after_dot = m.start() > 0 and t[m.start() - 1] == "."
        for s, e in _random_spans(m.group(), depth, after_dot):
            yield m.start() + s, m.start() + e, SECRET


# --- URLs, emails, identifiers ---------------------------------------------------------------------------------

_URL_USERINFO = re.compile(r"(?i)\b[a-z][a-z0-9+.\-]{1,20}://([^\s/?#@:]+:[^\s/?#]*)@")
# Any Unicode letter or digit in the local part and the domain (internationalised addresses: иван@почта.рф).
_EMAIL = re.compile(
    r"(?<![\w.%+\-])([\w.%+\-]+)@((?:[\w\-]+\.)+"
    # the TLD; a capital right after a lower-case TLD is the next word, glued (name@gmail.comEduardo)
    r"(?:[a-z]{2,24}(?![a-z0-9\-])|[A-Z][a-z]{1,23}(?![a-z0-9\-])|[A-Z]{2,24}(?![A-Za-z0-9\-])"
    r"|xn--[A-Za-z0-9\-]{2,59}(?![A-Za-z0-9\-])|[^\W\d_\x00-\x7f]{2,24}(?![\w\-])))"
)
_IN_URL_AUTHORITY = re.compile(r"(?i)[a-z][a-z0-9+.\-]{1,20}://[^\s/?#@]*$")
_RETINA = re.compile(r"[0-9]+x\.", re.I)
_UUID = re.compile(
    r"(?<![0-9A-Za-z])[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}(?![0-9A-Za-z])"
)


def _url_credentials(t: str, depth: int) -> Iterator[Span]:
    for m in _URL_USERINFO.finditer(t):
        yield m.start(1), m.end(1), URL_CREDENTIALS


def _emails(t: str, depth: int) -> Iterator[Span]:
    for m in _EMAIL.finditer(t):
        local, domain = m.group(1), m.group(2)
        if _RETINA.match(domain):  # logo@2x.png
            continue
        if local == "git" and t.startswith(":", m.end()):  # git@github.com:owner/repo.git
            continue
        if _IN_URL_AUTHORITY.search(t, max(0, m.start() - 300), m.start()):
            continue  # the "password@host" of scheme://user:password@host: [URL_CREDENTIALS] and the host
        yield m.start(), m.end(), EMAIL


_HEX_ADDRESS = re.compile(r"(?<![0-9A-Za-z])0x[0-9A-Fa-f]{40}(?![0-9A-Za-z])")


def _uuids(t: str, depth: int) -> Iterator[Span]:
    for m in _UUID.finditer(t):
        yield m.start(), m.end(), ID
    for m in _HEX_ADDRESS.finditer(t):  # an Ethereum-style account address
        yield m.start(), m.end(), ID


_NIR = re.compile(
    r"(?<![0-9A-Za-z])([12])[ ]?([0-9]{2})[ ]?(0[1-9]|1[0-2]|[2-9][0-9])[ ]?(2[ABab]|[0-9]{2})[ ]?"
    r"([0-9]{3})[ ]?([0-9]{3})[ ]?([0-9]{2})(?![0-9A-Za-z])"
)
_SSN = re.compile(r"(?<![0-9A-Za-z\-])(?!000|666|9[0-9]{2})[0-9]{3}-(?!00)[0-9]{2}-(?!0000)[0-9]{4}(?![0-9A-Za-z\-])")


def _national_ids(t: str, depth: int) -> Iterator[Span]:
    for m in _NIR.finditer(t):  # French social security number, with its key
        dept = m.group(4).upper().replace("2A", "19").replace("2B", "18")
        number = int(m.group(1) + m.group(2) + m.group(3) + dept + m.group(5) + m.group(6))
        if 97 - number % 97 == int(m.group(7)):
            yield m.start(), m.end(), ID
    for m in _SSN.finditer(t):  # US social security number, dashed form only
        yield m.start(), m.end(), ID


# --- IBANs and cards --------------------------------------------------------------------------------------------

_IBAN_START = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{2})([0-9]{2})")


def _ibans(t: str, depth: int) -> Iterator[Span]:
    n = len(t)
    for m in _IBAN_START.finditer(t):
        length = IBAN_LENGTHS.get(m.group(1).upper())
        if length is None:
            continue
        chars = [*m.group(1).upper(), *m.group(2)]
        i = m.end()
        while len(chars) < length and i < n:
            ch = t[i]
            if ch.isascii() and ch.isalnum():
                chars.append(ch.upper())
                i += 1
            elif ch == " " and i + 1 < n and t[i + 1].isascii() and t[i + 1].isalnum() and t[i - 1] != " ":
                i += 1
            else:
                break
        if len(chars) != length or (i < n and t[i].isascii() and t[i].isalnum()):
            continue
        if iban_ok("".join(chars)):
            yield m.start(), i, IBAN


# A run of digit groups separated by one space or dash, not glued to a word, a decimal or a phone's "+".
_DIGIT_RUN = re.compile(r"(?<![0-9A-Za-z+])(?<![0-9][.,])[0-9]+(?:[ \-][0-9]+)*(?![0-9A-Za-z]|[.,][0-9])")
_GROUP = re.compile(r"[0-9]+")


def _card_ok(digits: str) -> bool:
    return 13 <= len(digits) <= 19 and digits[0] in "23456" and len(set(digits)) > 1 and luhn_ok(digits)


def _card_grouping(sizes: list[int]) -> bool:
    """4-4-4-4 (and 4-4-4-4-3, 4-4-4-1...), or the 4-6-5 and 4-6-4 of Amex and Diners: not a table of numbers."""
    if sizes in ([4, 6, 5], [4, 6, 4]):
        return True
    return all(size == 4 for size in sizes[:-1]) and 1 <= sizes[-1] <= 4


def _cards(t: str, depth: int) -> Iterator[Span]:
    for run in _DIGIT_RUN.finditer(t):
        groups = [(g.start() + run.start(), g.end() + run.start()) for g in _GROUP.finditer(run.group())]
        if len(groups) == 1:
            if _card_ok(run.group()):
                yield run.start(), run.end(), CARD
            continue
        # grouped digits: the longest contiguous groups (each of 3 digits or more) that make a valid number
        best = None
        for i in range(len(groups)):
            for j in range(i, min(len(groups), i + 8)):
                part = groups[i : j + 1]
                digits = "".join(t[s:e] for s, e in part)
                if len(digits) > 19:
                    break
                if (
                    _card_grouping([e - s for s, e in part])
                    and _card_ok(digits)
                    and (best is None or part[-1][1] - part[0][0] > best[1] - best[0])
                ):
                    best = (part[0][0], part[-1][1])
        if best:
            yield best[0], best[1], CARD


# --- IP addresses -----------------------------------------------------------------------------------------------

_IPV4 = re.compile(r"(?<![0-9A-Za-z.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9A-Za-z]|\.[0-9])")
_IPV6 = re.compile(
    r"(?<![0-9A-Za-z:.])(?:[0-9A-Fa-f]{0,4}:){2,7}(?:(?:[0-9]{1,3}\.){3}[0-9]{1,3}|[0-9A-Fa-f]{1,4})?"
    r"(?:%[0-9A-Za-z_.\-]+)?(?![0-9A-Za-z:])"
)
_NETMASK_OCTETS = {"0", "128", "192", "224", "240", "248", "252", "254", "255"}


def _ipv4s(t: str, depth: int) -> Iterator[Span]:
    for m in _IPV4.finditer(t):
        try:
            address = ipaddress.IPv4Address(m.group())
        except ValueError:
            continue
        octets = m.group().split(".")
        if address.is_loopback or address.is_unspecified or (octets[0] == "255" and set(octets) <= _NETMASK_OCTETS):
            continue  # 127.0.0.1, 0.0.0.0, 255.255.255.0: no one's address
        if octets[3] == "0" or all(len(o) == 1 for o in octets):
            continue  # a network (10.0.0.0) or a section number (5.1.2.1), not a host
        if m.start() >= 2 and t[m.start() - 1] == "/" and t[m.start() - 2].isalpha():
            continue  # a product version: Chrome/110.0.5481.77
        yield m.start(), m.end(), IP


def _ipv6s(t: str, depth: int) -> Iterator[Span]:
    for m in _IPV6.finditer(t):
        candidate = m.group().split("%", 1)[0]
        if not any(ch.isdigit() for ch in candidate) or sum(1 for g in re.split(r"[:.]", candidate) if g) < 2:
            continue  # dead::beef, C++ scopes like a::b, and a lone "::2"
        try:
            address = ipaddress.IPv6Address(candidate)
        except ValueError:
            continue
        if address.is_loopback or address.is_unspecified:
            continue
        yield m.start(), m.end(), IP


# --- phone numbers ----------------------------------------------------------------------------------------------

# A run of digit groups with one separator between groups; a group may be in parentheses, like "(0)30" or "(415)".
_PHONE_RUN = re.compile(
    r"(?<![0-9A-Za-z+/\\])(?<![0-9][.,:])\+?\(?[0-9]+(?:\)[0-9]*)?(?:[ .\-/]\(?[0-9]+(?:\)[0-9]*)?)*"
)
_PHONE_GROUP = re.compile(r"\(?[0-9]+(?:\)[0-9]*)?")
_PHONE_MAX_GROUPS = 8


def _phone_shape(parts: list[str], seps: list[str], plus: bool) -> bool:
    """Whether these groups (and the separators between them) look like a phone number."""
    digits = "".join(ch for part in parts for ch in part if ch.isdigit())
    n = len(digits)
    kinds = set(seps)
    sizes = [sum(ch.isdigit() for ch in part) for part in parts]
    parens = any("(" in part or ")" in part for part in parts)
    if any(part.count("(") != part.count(")") for part in parts):
        return False  # "(09-26)" is a date in brackets, not "(030)"
    if set(digits) <= {"0", "1"}:
        return False  # binary: 0010 0011 0101
    if plus:  # E.164: country code, 8 to 15 digits, spaces plus at most one other separator
        return 8 <= n <= 15 and digits[0] != "0" and "/" not in kinds and len(kinds - {" "}) <= 1
    if parens:
        # (415) 555-0132, 8 (912) 345-67-89, (030) 1234567: the area code in brackets, at the start
        first_bracket = next(k for k, part in enumerate(parts) if "(" in part)
        if first_bracket > 1 or "/" in kinds:
            return False  # 06/04/1983 (39) is a date and an age
        if digits.startswith("0"):
            return 9 <= n <= 12 and len(kinds - {" "}) <= 1
        if digits.startswith("8") and n == 11:
            return True
        return n == 10 and digits[0] in "23456789" and parts[0].startswith("(") and len(kinds - {" "}) <= 1
    if len(kinds) > 1:
        # mixed separators: only the Russian 8 912 345-67-89 shape (a date and a time would also mix them)
        return n == 11 and digits[0] == "8" and kinds <= {" ", "-"} and sizes[0] == 1
    if digits.startswith("00"):  # 00 49 30 1234567 (not 0043585/2012, a file number)
        return 10 <= n <= 17 and digits[2] != "0" and min(sizes) >= 2 and "/" not in kinds
    if digits.startswith("0"):  # national trunk prefix: 06 12 34 56 78, 030 12345678, 020 7946 0958
        if set(sizes) == {2}:
            # pairs: ten digits (06 12 34 56 78), and not a rising draw of lottery numbers (03 04 13 29 36 43)
            values = [int(part) for part in parts]
            draw = all(a < b for a, b in pairwise(values)) and max(values) <= 59
            return n == 10 and not draw
        return 9 <= n <= 12 and digits[1] != "0" and min(sizes) >= 2
    if n == 11 and digits[0] == "1" and digits[1] in "3456789":  # Chinese mobile, 138 0013 8000
        return sizes in ([11], [3, 4, 4]) and kinds <= {" ", "-"}
    if n == 11 and digits[0] == "8" and digits[1] in "3456789":  # Russian national, 89123456789
        return sizes in ([11], [1, 3, 3, 2, 2], [1, 3, 3, 4]) and kinds <= {" ", "-"}
    if n == 10 and digits[0] == "3" and kinds <= {" "} and sizes in ([3, 3, 4], [3, 7], [3, 4, 3]):
        return True  # Italian mobile, 333 123 4567
    if n == 10 and digits[0] in "23456789":  # US 415-555-0132, 415.555.0132 (never spaces alone: too common)
        return sizes == [3, 3, 4] and kinds in ({"-"}, {"."})
    if n == 11 and digits[0] == "1":  # 1-415-555-0132
        return sizes == [1, 3, 3, 4] and kinds in ({"-"}, {"."})
    return False


def _phones(t: str, depth: int) -> Iterator[Span]:
    for run in _PHONE_RUN.finditer(t):
        text = run.group()
        plus = text.startswith("+")
        offset = run.start() + (1 if plus else 0)
        groups = [(g.start() + offset, g.end() + offset) for g in _PHONE_GROUP.finditer(text[1:] if plus else text)]
        # from the first group, the longest part of the run that is a phone ("06 12 34 56 78 2 fois"), then again
        # right after it (two numbers in a row); a run whose start is not a phone is not searched further, so a
        # number inside a longer number (a bad IBAN, a reference) is not taken for a phone
        i = 0
        while i < len(groups):
            for size in range(min(_PHONE_MAX_GROUPS, len(groups) - i), 0, -1):
                part = groups[i : i + size]
                parts = [t[s:e] for s, e in part]
                seps = [t[part[k][1] : part[k + 1][0]] for k in range(size - 1)]
                with_plus = plus and i == 0
                if i + size < len(groups) and t[part[-1][1] : groups[i + size][0]] != " ":
                    continue  # a part cut out of "0422/100744.587076" (a log timestamp) is not a phone
                if _phone_shape(parts, seps, with_plus):
                    start, end = part[0][0] - (1 if with_plus else 0), part[-1][1]
                    if t[start] == "(" and ")" not in t[start:end]:
                        start += 1
                    if t[end - 1] == ")" and "(" not in t[start : end - 1]:
                        end -= 1
                    yield start, end, PHONE
                    i += size
                    break
            else:
                break


# --- all together -----------------------------------------------------------------------------------------------

# Every detector runs; overlapping findings are merged (union, longest marker).
_DETECTORS: list[Callable[[str, int], Iterator[Span]]] = [
    _secrets_prefixed,
    _url_credentials,
    _emails,
    _uuids,
    _ibans,
    _cards,
    _national_ids,
    _ipv6s,
    _ipv4s,
    _phones,
    _secrets_by_keyword,
    _secrets_by_entropy,
]


def merge_spans(spans: list[Span], length: int) -> list[Span]:
    """The union of the spans: overlapping spans become one, whose marker is that of the longest.

    Spans are clamped to the text; empty ones are dropped. Spans that only touch stay separate. Among spans of the
    same length, the first one (by start) gives the marker.
    """
    clamped = sorted((max(0, s), min(length, e), m) for s, e, m in spans if min(length, e) > max(0, s))
    merged: list[Span] = []
    longest = 0
    for start, end, marker in clamped:
        if merged and start < merged[-1][1]:
            s0, e0, m0 = merged[-1]
            if end - start > longest:
                longest, m0 = end - start, marker
            merged[-1] = (s0, max(e0, end), m0)
        else:
            merged.append((start, end, marker))
            longest = end - start
    return merged


def _find(t: str, depth: int) -> list[Span]:
    spans = [span for detector in _DETECTORS for span in detector(t, depth)]
    return merge_spans(spans, len(t))


def find_patterns(text: str) -> list[Span]:
    """Spans of emails, phones, IBANs, cards, IPs, URL credentials, identifiers and secrets.

    Overlapping findings are merged into one span (their union), marked by the longest: a span is never dropped.
    """
    return _find(normalise(text), 0)
