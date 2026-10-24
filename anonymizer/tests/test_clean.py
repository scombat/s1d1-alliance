"""anonymize() and clean_line(): the merge of spans and the shape of a clean line (decision 11)."""

from __future__ import annotations

import hashlib
import json
import random
import re
import string

import pytest
from fakes import FIXTURES, FakeDetector, NoDetector, clean_expected_text, raw_valid_lines

from anonymizer.canonical import canonical
from anonymizer.clean import CLEAN_KEYS, MAX_PROMPT_CHARS, anonymize, clean_line, clean_line_reason, dumps_line
from anonymizer.coverage import is_uncovered, letter_scripts

DECISION_11 = [
    "id",
    "time",
    "prompt",
    "source",
    "model_revision",
    "terms_version",
    "format",
    "pred_tier",
    "pred_score",
    "pred_task",
    "pred_needs_tools",
    "pred_confidence",
]


def test_overlaps_merge_longest_wins():
    text = "Anna Maria Schmidt, Hauptstrasse 5, Berlin, ok"
    # PERSON 0-18 and ADDRESS 11-42 overlap: one span 0-42, the longer (ADDRESS) gives the marker
    detector = FakeDetector([(0, 18, "[PERSON]"), (11, 42, "[ADDRESS]")])
    assert anonymize(text, detector) == "[ADDRESS], ok"
    # a span inside a longer one disappears into it
    detector = FakeDetector([(0, 18, "[PERSON]"), (5, 10, "[PHONE]")])
    assert anonymize(text, detector) == "[PERSON], Hauptstrasse 5, Berlin, ok"
    # spans that only touch stay two replacements, applied right to left
    detector = FakeDetector([(0, 4, "[PERSON]"), (4, 10, "[ID]")])
    assert anonymize(text, detector) == "[PERSON][ID] Schmidt, Hauptstrasse 5, Berlin, ok"
    # order of the spans does not matter, nor duplicates
    detector = FakeDetector([(11, 42, "[ADDRESS]"), (0, 18, "[PERSON]"), (0, 18, "[PERSON]")])
    assert anonymize(text, detector) == "[ADDRESS], ok"


def test_model_and_patterns_are_merged():
    text = "Jane Doe <jane@example.com>"
    detector = FakeDetector([(0, 8, "[PERSON]"), (10, 26, "[EMAIL]")])
    assert anonymize(text, detector) == "[PERSON] <[EMAIL]>"
    # the patterns act even when the model misses
    assert anonymize(text, FakeDetector([(0, 8, "[PERSON]")])) == "[PERSON] <[EMAIL]>"
    # a model span that overlaps a pattern span: the union is replaced, the longer gives the marker
    detector = FakeDetector([(5, 15, "[PERSON]")])
    assert anonymize(text, detector) == "Jane [EMAIL]>"


def test_model_spans_are_clamped_to_the_text_and_empty_ones_dropped():
    text = "hello world"
    detector = FakeDetector([(5, 5, "[PERSON]"), (-3, 2, "[PERSON]"), (8, 99, "[PERSON]"), (3, 1, "[PERSON]")])
    assert anonymize(text, detector) == "[PERSON]llo wo[PERSON]"


def test_clean_line_has_exactly_the_decision_11_keys():
    raw = raw_valid_lines()[1]
    out = clean_line(raw, NoDetector())
    assert list(out) == DECISION_11
    assert list(CLEAN_KEYS) == DECISION_11
    assert "instance_id" not in out and "received" not in out and "variant" not in out and "v" not in out
    assert out["time"] == "2026-10-21"
    assert out["source"] == "capture:selfhosted:a3b1c2d4-5e6f-4a7b-8c9d-0e1f2a3b4c5d"


def test_clean_line_anonymises_the_prompt_only():
    raw = {**raw_valid_lines()[0], "prompt": "Mail jane@example.com please", "ip": "203.0.113.9", "headers": {"a": 1}}
    out = clean_line(raw, NoDetector())
    assert out["prompt"] == "Mail [EMAIL] please"
    assert out["id"] == raw["id"] and out["pred_score"] == raw["pred_score"]
    assert "ip" not in out and "headers" not in out


def test_clean_line_without_prompt_is_none():  # and nothing but a dict is a line
    base = raw_valid_lines()[0]
    assert clean_line({k: v for k, v in base.items() if k != "prompt"}, NoDetector()) is None
    assert clean_line({**base, "prompt": ""}, NoDetector()) is None
    assert clean_line({**base, "prompt": 42}, NoDetector()) is None
    assert clean_line("not a dict", NoDetector()) is None


def test_clean_line_time_falls_back_to_the_received_day():
    base = raw_valid_lines()[2]
    without_time = {k: v for k, v in base.items() if k != "time"}
    assert clean_line(without_time, NoDetector())["time"] == "2026-10-24"
    assert clean_line({**base, "time": "yesterday"}, NoDetector())["time"] == "2026-10-24"
    neither = {k: v for k, v in without_time.items() if k != "received"}
    assert clean_line(neither, NoDetector())["time"] is None


def test_missing_optional_keys_are_null():
    raw = {"v": 1, "id": UUID, "prompt": "hi", "terms_version": "v1", "source": "capture:direct", "pred_score": None}
    out = clean_line(raw, NoDetector())
    assert list(out) == DECISION_11
    assert out == {
        **dict.fromkeys(DECISION_11),
        "id": UUID,
        "prompt": "hi",
        "source": "capture:direct",
        "terms_version": "v1",
    }


UUID = "8b8c846f-d861-4d21-a86b-4582c8e5209a"


def _base(**changes):
    return {**raw_valid_lines()[0], **changes}


def test_metadata_outside_the_known_values_becomes_null():
    out = clean_line(
        _base(format="soap", pred_tier="GIGANTIC", pred_task="general_qa", model_revision="main; rm -rf /"),
        NoDetector(),
    )
    assert out["format"] is None and out["pred_tier"] is None and out["pred_task"] is None
    assert out["model_revision"] is None
    for tier in ("TRIVIAL", "LOW", "MEDIUM", "HIGH", "EXPERT", "SIMPLE", "COMPLEX", "REASONING"):
        assert clean_line(_base(pred_tier=tier), NoDetector())["pred_tier"] == tier
    for fmt in ("systemone", "decisions", "chat", "classify", "predict"):
        assert clean_line(_base(format=fmt), NoDetector())["format"] == fmt
    assert clean_line(_base(pred_task="coding_debugging"), NoDetector())["pred_task"] == "coding_debugging"
    assert clean_line(_base(model_revision="3c9e1f2a" * 5), NoDetector())["model_revision"] == "3c9e1f2a" * 5
    assert clean_line(_base(model_revision="3C9E1F2"), NoDetector())["model_revision"] is None


def test_probabilities_must_be_finite_numbers_in_0_1():
    for bad in (True, False, -0.1, 1.5, float("nan"), float("inf"), "0.5", [0.5]):
        out = clean_line(_base(pred_score=bad, pred_needs_tools=bad, pred_confidence=bad), NoDetector())
        assert out["pred_score"] is None and out["pred_needs_tools"] is None and out["pred_confidence"] is None, bad
    out = clean_line(_base(pred_score=1, pred_needs_tools=0, pred_confidence=0.5), NoDetector())
    assert (out["pred_score"], out["pred_needs_tools"], out["pred_confidence"]) == (1.0, 0.0, 0.5)
    assert isinstance(out["pred_score"], float)


def test_an_unknown_source_or_terms_version_drops_the_line():
    for source in ("capture:evil", "capture:selfhosted:NOT-A-UUID", f"capture:selfhosted:{UUID.upper()}", None, 3):
        assert clean_line_reason(_base(source=source), NoDetector()) == (None, "dropped"), source
    for terms in ("1", "v", "v1234", "v1; drop", None, 1):
        assert clean_line_reason(_base(terms_version=terms), NoDetector()) == (None, "dropped"), terms
    assert clean_line(_base(source=f"capture:selfhosted:{UUID}"), NoDetector())["source"].endswith(UUID)
    assert clean_line(_base(terms_version="v12"), NoDetector())["terms_version"] == "v12"


def test_an_id_that_is_not_a_uuid_is_hashed():
    assert clean_line(_base(id=UUID), NoDetector())["id"] == UUID
    assert clean_line(_base(id=UUID.upper()), NoDetector())["id"] == UUID
    expected = hashlib.sha256(b"jane@example.com").hexdigest()[:32]
    assert clean_line(_base(id="jane@example.com"), NoDetector())["id"] == expected
    assert re.fullmatch(r"[0-9a-f]{32}", clean_line(_base(id="order 42"), NoDetector())["id"])
    for unusable in (None, "", 42, ["x"]):
        assert clean_line_reason(_base(id=unusable), NoDetector()) == (None, "dropped"), unusable
    assert clean_line_reason({k: v for k, v in _base().items() if k != "id"}, NoDetector()) == (None, "dropped")


def test_time_must_be_a_real_calendar_date():
    assert clean_line(_base(time="2026-02-30T10:00:00Z", received="2026-10-24"), NoDetector())["time"] == "2026-10-24"
    assert clean_line(_base(time="2026-13-01", received="2026-99-99"), NoDetector())["time"] is None
    assert clean_line(_base(time="2024-02-29T00:00:00Z"), NoDetector())["time"] == "2024-02-29"


def test_a_prompt_mostly_in_an_uncovered_script_is_dropped():
    detector = FakeDetector([(0, 3, "[PERSON]")])
    for prompt in (
        "Меня зовут Ольга, я живу в Казани.",  # Cyrillic
        "東京都に住んでいる山田です。",  # Japanese
        "मेरा नाम राहुल है।",  # Devanagari
        "ผมชื่อสมชาย",  # Thai
        "Მე მქვია გიორგი.",  # Georgian: never tested, so not covered
    ):
        assert clean_line_reason(_base(prompt=prompt), detector) == (None, "dropped_uncovered_script"), prompt
    assert detector.calls == 0  # dropped before the model runs
    for prompt in (
        "我叫王伟。",
        "اسمي ليلى.",
        "שמי דוד.",
        "Με λένε Γιώργο.",
        "저는 김민준입니다.",
        "Je m'appelle Léa.",
    ):
        assert clean_line(_base(prompt=prompt), NoDetector()) is not None, prompt


def _mixed_cases():
    for raw in (FIXTURES / "mixed_scripts.txt").read_text(encoding="utf-8").splitlines():
        if raw.strip() and not raw.startswith("#"):
            yield raw.split("\t", 1)


@pytest.mark.parametrize(("expected", "prompt"), list(_mixed_cases()))
def test_one_uncovered_letter_drops_the_line(expected, prompt):
    # one letter in an uncovered script drops the line, whatever surrounds it: a name can be two letters ("Ли Хо")
    _, reason = clean_line_reason(_base(prompt=prompt), NoDetector())
    assert reason == ("dropped_uncovered_script" if expected == "drop" else None), prompt


def test_kana_are_counted_among_cjk_letters_only():
    padded = "Please check this line: " * 10 + "東京都新宿区西新宿に住んでいます"
    assert letter_scripts(padded).get("JAPANESE", 0) == 16
    assert "HAN" not in letter_scripts(padded)
    assert is_uncovered(padded)
    assert not is_uncovered("请把这段代码改成支持 Python 3.12 的版本。")


def test_valid_fixture_matches_clean_expected():
    detector = NoDetector()
    produced = "".join(dumps_line(clean_line(raw, detector)) + "\n" for raw in raw_valid_lines())
    assert produced == clean_expected_text()
    assert [json.loads(x) for x in produced.splitlines()] == [json.loads(x) for x in clean_expected_text().splitlines()]


# --- the canonical form: look-alike letters and digits, invisible separators -----------------------------------


def _unescape(text):
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text)


def _lookalike_cases():
    for raw in (FIXTURES / "lookalikes.txt").read_text(encoding="utf-8").splitlines():
        if raw.strip() and not raw.startswith("#"):
            expected, prompt = raw.split("\t", 1)
            yield _unescape(expected), _unescape(prompt)


@pytest.mark.parametrize(("expected", "prompt"), list(_lookalike_cases()))
def test_lookalikes_are_folded_before_anonymising(expected, prompt):
    out, reason = clean_line_reason(_base(prompt=prompt), NoDetector())
    if expected == "drop":
        assert (out, reason) == (None, "dropped_uncovered_script"), prompt
    else:
        assert out["prompt"] == expected, prompt


class _SeesJohnSmith(NoDetector):
    """A model that finds "John smith" in plain letters, and only there: what it reads is the canonical form."""

    def spans(self, text):
        self.calls += 1
        return [(m.start(), m.end(), "[PERSON]") for m in re.finditer("John smith", text)]


def test_the_models_read_the_folded_text():
    # modifier and superscript letters fold to plain Latin before the model reads them; small capitals are dropped
    detector = _SeesJohnSmith()
    assert clean_line(_base(prompt="Ask ᴶᵒʰⁿ ˢᵐⁱᵗʰ for the keys."), detector)["prompt"] == "Ask [PERSON] for the keys."
    assert clean_line_reason(_base(prompt="Ask ᴊᴏʜɴ sᴍɪᴛʜ for the keys."), detector) == (
        None,
        "dropped_uncovered_script",
    )
    assert detector.calls == 1


def test_a_zero_width_space_inside_a_key_does_not_hide_it():
    # assembled at run time so that gitleaks sees no key-shaped literal; not a real credential
    rng = random.Random(7)
    body = "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(40))
    for invisible in ("\u200b", "\u2060", "\u00ad", "\ufeff", "\u200d", "\u200c"):
        key = "sk" + "-" + "proj-" + body[:20] + invisible + body[20:]
        out = clean_line(_base(prompt=f"my key is {key} ok"), NoDetector())
        assert out["prompt"] == "my key is [SECRET] ok", repr(invisible)


def test_the_canonical_form_keeps_emoji_sequences_and_persian():
    assert canonical("ok \U0001f9d1\U0001f3fd\u200d\U0001f4bb ok") == "ok \U0001f9d1\U0001f3fd\u200d\U0001f4bb ok"
    assert canonical("a\u200db") == "ab"  # a joiner between letters splits nothing worth keeping
    assert (
        canonical("\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645")
        == "\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645"
    )
    assert canonical("\u0645\u06cc\u200c") == "\u0645\u06cc"


def test_a_prompt_that_nfkc_makes_too_long_or_empty_is_dropped():
    # one U+FDFA gives 18 characters: the canonical form must still fit the contract's bound
    long_prompt = "\ufdfa" * (MAX_PROMPT_CHARS // 18 + 1)
    assert len(long_prompt) <= MAX_PROMPT_CHARS < len(canonical(long_prompt))
    assert clean_line_reason(_base(prompt=long_prompt), NoDetector()) == (None, "dropped")
    assert clean_line_reason(_base(prompt="\u200b\ufeff"), NoDetector()) == (None, "dropped")
