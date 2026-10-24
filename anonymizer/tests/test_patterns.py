"""The validated patterns: what they find, what they must leave alone."""

from __future__ import annotations

import base64
import random
import string
import time

import pytest
from fakes import FIXTURES, NoDetector

from anonymizer.clean import anonymize
from anonymizer.patterns import find_patterns, iban_ok, luhn_ok, shannon_entropy


def _cases(name):
    for raw in (FIXTURES / name).read_text(encoding="utf-8").splitlines():
        if raw.strip() and not raw.startswith("#"):
            yield raw


def _markers(text):
    return [marker for _, _, marker in sorted(find_patterns(text))]


def _random_token(rng, n=40, alphabet=string.ascii_letters + string.digits):
    while True:
        token = "".join(rng.choice(alphabet) for _ in range(n))
        if any(c.isdigit() for c in token) and any(c.isalpha() for c in token):
            return token


# Secrets are assembled at run time so that the secret scanner of the repository (gitleaks) sees no key-shaped
# literal in this file. None of them is a real credential.
_RNG = random.Random(20261024)
_ALNUM = string.ascii_letters + string.digits
OPENAI_LIKE = "sk-" + "proj-" + _random_token(_RNG, 48)
GITHUB_LIKE = "gh" + "p_" + _random_token(_RNG, 36)
GITHUB_PAT_LIKE = "github" + "_pat_" + _random_token(_RNG, 22) + "_" + _random_token(_RNG, 59)
AWS_LIKE = "AK" + "IA" + "".join(_RNG.choice(string.ascii_uppercase + string.digits) for _ in range(16))
SLACK_LIKE = "xo" + "xb-" + "1234567890-" + _random_token(_RNG, 24)
RANDOM_40 = _random_token(_RNG, 40)
PEM = (
    "-----BEGIN "
    + "RSA PRIVATE"
    + " KEY-----\n"
    + "\n".join(_random_token(_RNG, 64, _ALNUM + "+/") for _ in range(3))
    + "\n-----END "
    + "RSA PRIVATE"
    + " KEY-----"
)


# --- each fixture line gives exactly the expected markers -------------------------------------------------


@pytest.mark.parametrize("line", list(_cases("patterns.txt")))
def test_email_phone_iban_card_ip_are_found(line):
    expected, text = line.split("\t", 1)
    assert _markers(text) == expected.split(), text


def test_spans_cover_the_value_only():
    text = "Mail jane@example.com now, call +33 6 12 34 56 78."
    assert anonymize(text, NoDetector()) == "Mail [EMAIL] now, call [PHONE]."
    text = "Use postgres://admin:s3cretPass@db.example.com:5432/app"
    assert anonymize(text, NoDetector()) == "Use postgres://[URL_CREDENTIALS]@db.example.com:5432/app"
    assert anonymize("IBAN FR76 3000 6000 0112 3456 7890 189 merci", NoDetector()) == "IBAN [IBAN] merci"


def test_luhn_and_mod97_reject_random_digits():
    assert luhn_ok("4111111111111111")
    assert not luhn_ok("4111111111111112")
    assert iban_ok("FR7630006000011234567890189")
    assert iban_ok("GB29 NWBK 6016 1331 9268 19")
    assert not iban_ok("FR7630006000011234567890188")
    assert "[CARD]" not in _markers("4111 1111 1111 1112")
    assert anonymize("4111 1111 1111 1112", NoDetector()) == "4111 1111 1111 1112"
    bad_iban = "FR76 3000 6000 0112 3456 7890 188"
    assert anonymize(bad_iban, NoDetector()) == bad_iban
    rng = random.Random(7)
    for _ in range(200):
        digits = "".join(rng.choice("0123456789") for _ in range(16))
        text = f"ref {digits[:4]} {digits[4:8]} {digits[8:12]} {digits[12:]}"
        assert ("[CARD]" in _markers(text)) == (digits[0] in "23456" and luhn_ok(digits))


def test_secrets_by_prefix_and_entropy():
    for secret in (OPENAI_LIKE, GITHUB_LIKE, GITHUB_PAT_LIKE, AWS_LIKE, SLACK_LIKE, RANDOM_40, PEM):
        assert anonymize(f"my key is {secret} ok", NoDetector()) == "my key is [SECRET] ok", secret
    assert shannon_entropy(RANDOM_40) >= 4.0
    words = "internationalizationandlocalizationtask"  # 40 letters, an English word run
    assert len(words) >= 39
    assert anonymize(f"see {words}", NoDetector()) == f"see {words}"
    words_digits = "internationalization2localization3tasks"
    assert anonymize(f"see {words_digits}", NoDetector()) == f"see {words_digits}"


def test_secrets_in_context():
    jwt = (
        "ey"
        + "JhbGciOiJIUzI1NiJ9."
        + "ey"
        + "JzdWIiOiIxMjM0NTY3ODkwIn0."
        + "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    )
    cases = {
        f'curl -H "Authorization: Bearer {RANDOM_40}" https://api.example.com': (
            'curl -H "Authorization: Bearer [SECRET]" https://api.example.com'
        ),
        f"token {jwt} expired": "token [SECRET] expired",
        'password = "hunter2!"': 'password = "[SECRET]"',
        "DB_PASSWORD=Tr0ub4dor&3 in .env": "DB_PASSWORD=[SECRET] in .env",
        "mon mot de passe est Soleil2024! merci": "mon mot de passe est [SECRET] merci",
        "my password is hunter22.": "my password is [SECRET].",
        '{"api_key": "' + "abcd" + "1234efgh" + '"}': '{"api_key": "[SECRET]"}',  # split for gitleaks
    }
    for text, expected in cases.items():
        assert anonymize(text, NoDetector()) == expected, text


def test_entropy_catches_random_tokens_and_spares_text():
    rng = random.Random(1)
    alphabets = (_ALNUM, _ALNUM + "+/", _ALNUM + "-_")
    misses = 0
    for i in range(600):
        token = _random_token(rng, rng.choice((32, 40, 48, 64)), alphabets[i % 3])
        if anonymize(f"x {token} y", NoDetector()) != "x [SECRET] y":
            misses += 1
    assert misses <= 3  # at most 0.5 % of random tokens pass as identifiers
    # base64 of a short sentence is text, not a secret; base64 of random bytes is a secret
    short = base64.b64encode("Please summarise the attached report in French".encode()).decode()
    assert anonymize(f"decode {short}", NoDetector()) == f"decode {short}"
    random_bytes = base64.b64encode(bytes(rng.randrange(256) for _ in range(30))).decode()
    assert anonymize(f"decode {random_bytes}", NoDetector()) == "decode [SECRET]"
    # base64 of text that carries an email is treated as a secret
    carrier = base64.b64encode(b"write to jane.doe@example.com today please").decode()
    assert anonymize(f"decode {carrier}", NoDetector()) == "decode [SECRET]"


@pytest.mark.parametrize("line", list(_cases("clean.txt")))
def test_clean_lines_are_unchanged(line):
    assert anonymize(line, NoDetector()) == line, find_patterns(line)


def test_clean_text_is_unchanged_in_five_scripts():
    sentences = [
        "Le rendez-vous est à 14h30 le 24/10/2026 ; prévoir 3 000 € et 2 heures.",
        "How do I fix an off-by-one error in a for loop over 10 000 items?",
        "请把这段代码改成支持 Python 3.12 的版本，大约需要 200 行。",
        "كم عدد أيام السنة الكبيسة؟ الجواب ٣٦٦ يومًا، أي 366 يومًا.",
        "Сколько будет 2+2? Ответ: 4. Напишите функцию на 50 строк.",
    ]
    for sentence in sentences:
        assert anonymize(sentence, NoDetector()) == sentence


def test_uuid_in_the_prompt_is_an_identifier():
    text = "Look up 8b8c846f-d861-4d21-a86b-4582c8e5209a in the logs."
    assert anonymize(text, NoDetector()) == "Look up [ID] in the logs."


def test_long_adversarial_prompts_stay_fast():
    shapes = [
        "a" * 65_536,
        ("x" * 63 + "@") * 1024,
        "+1234567890123456789 " * 3000,
        "1 " * 32_000,
        "0" * 65_536,
        "::" * 32_000,
        "1.2.3.4." * 8000,
        "password=" * 7000,
        ("A1b2" * 16 + " ") * 1000,
        "-----BEGIN X-----" * 3800,
    ]
    for text in shapes:
        start = time.perf_counter()
        find_patterns(text[:65_536])
        assert time.perf_counter() - start < 3.0, text[:30]
