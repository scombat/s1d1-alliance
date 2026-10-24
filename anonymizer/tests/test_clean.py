"""anonymize() and clean_line(): the merge of spans and the shape of a clean line (decision 11)."""

from __future__ import annotations

import json

from fakes import FakeDetector, NoDetector, clean_expected_text, raw_valid_lines

from anonymizer.clean import CLEAN_KEYS, anonymize, clean_line, dumps_line

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


def test_clean_line_without_prompt_is_none():
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
    assert "time" not in clean_line(neither, NoDetector())


def test_missing_optional_keys_are_left_out():
    raw = {"v": 1, "id": "a", "prompt": "hi", "terms_version": "v1", "source": "capture:direct", "pred_score": None}
    assert clean_line(raw, NoDetector()) == {
        "id": "a",
        "prompt": "hi",
        "source": "capture:direct",
        "terms_version": "v1",
    }


def test_valid_fixture_matches_clean_expected():
    detector = NoDetector()
    produced = "".join(dumps_line(clean_line(raw, detector)) + "\n" for raw in raw_valid_lines())
    assert produced == clean_expected_text()
    assert [json.loads(x) for x in produced.splitlines()] == [json.loads(x) for x in clean_expected_text().splitlines()]
