"""Which writing systems the anonymiser is known to cover, and the fail-closed rule for the others.

A script is covered when, in the slow tests (`tests/fixtures/pii/`), the model ensemble masks every name and every
address of every fixture written in it. A prompt with a single letter in a script that is not covered (tested and
missed, or never tested) is dropped rather than stored: the models miss names in those scripts, and a name can be two
letters long ("Ли Хо"), so no count or share of such letters is safe. What the anonymiser cannot vouch for does not
enter the permanent corpus. No I/O, no logging.
"""

from __future__ import annotations

import unicodedata
from functools import lru_cache

# Covered by the fixtures of tests/fixtures/pii/ (see the slow tests and the README). Change only with a fixture.
COVERED_SCRIPTS = frozenset({"LATIN", "GREEK", "HEBREW", "ARABIC", "HAN", "HANGUL"})
# Tested and missed at the pinned revisions: Cyrillic (ru.txt: the address), Japanese (ja.txt: the address),
# Devanagari (hi.txt: name and address), Thai (th.txt: name and address). Any other script was never tested.
# Letters that belong to no script of their own, and hold no name: ordinal indicators (nº, 1ª), modifier letters
# (IPA ˈ ː), the micro sign, letterlike symbols (ℝ ℏ ℓ ℹ), super- and subscript letters. They count as COMMON, covered.
COMMON = "COMMON"
# Small capitals (ᴊᴏʜɴ, IPA ɪ ʀ), and the modifier capitals that NFKC leaves as they are (ᴯ ᴻ): a name can be spelt in
# them, NFKC does not fold them to plain letters (`canonical.py`) and no fixture shows the models read them, so they are
# not covered.
SMALL_CAPITAL = "SMALL_CAPITAL"
_KANA_SHARE = 0.05  # kana / (kana + Han) from which the Han characters are read as Japanese


@lru_cache(maxsize=65_536)
def script_of(ch: str) -> str:
    """The script of a letter, from its Unicode name: LATIN, CYRILLIC, HAN, HIRAGANA, ARABIC... (UNKNOWN if none)."""
    try:
        name = unicodedata.name(ch)
    except ValueError:
        return "UNKNOWN"
    if name.startswith(("CJK UNIFIED IDEOGRAPH", "CJK COMPATIBILITY IDEOGRAPH", "IDEOGRAPHIC")):
        return "HAN"
    if name.startswith(("HALFWIDTH KATAKANA", "KATAKANA")):
        return "KATAKANA"  # also the prolonged sound mark, KATAKANA-HIRAGANA PROLONGED SOUND MARK
    if name.startswith(("HALFWIDTH HANGUL", "HANGUL")):
        return "HANGUL"
    if "SMALL CAPITAL" in name or name.startswith("MODIFIER LETTER CAPITAL"):
        return SMALL_CAPITAL
    if name.startswith(("MODIFIER LETTER", "FEMININE ORDINAL", "MASCULINE ORDINAL", "MICRO SIGN")) or (
        0x2100 <= ord(ch) <= 0x214F  # letterlike symbols
    ):
        return COMMON
    if name.startswith(("SUPERSCRIPT LATIN", "SUBSCRIPT LATIN")):
        return COMMON
    if name.startswith(("FULLWIDTH LATIN", "MATHEMATICAL", "LATIN")):
        return "LATIN"
    return name.split(" ", 1)[0]


def letter_scripts(text: str) -> dict[str, int]:
    """Letters per script; Han counts as JAPANESE when kana are 5 % of the CJK letters (kana and Han).

    The share is taken over CJK letters only, so Latin padding around Japanese cannot make its Han read as Chinese.
    """
    counts: dict[str, int] = {}
    for ch in text:
        if ch.isalpha():
            script = script_of(ch)
            counts[script] = counts.get(script, 0) + 1
    kana = counts.get("HIRAGANA", 0) + counts.get("KATAKANA", 0)
    cjk = kana + counts.get("HAN", 0)
    if kana and kana / cjk >= _KANA_SHARE:
        counts["JAPANESE"] = counts.pop("HAN", 0) + counts.pop("HIRAGANA", 0) + counts.pop("KATAKANA", 0)
    return counts


def is_uncovered(text: str) -> bool:
    """True as soon as one letter is in a script the anonymiser does not cover (Han read as Japanese included).

    It reads the canonical form of the prompt (`canonical.py`), in which NFKC has folded the look-alike letters.
    """
    return any(script not in COVERED_SCRIPTS and script != COMMON for script in letter_scripts(text))
