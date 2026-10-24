"""The GLiNER adapter: word splitting, windows and offsets (fast, with a fake model), then the real model (slow)."""

from __future__ import annotations

import os
import re
import time

import pytest
from fakes import FIXTURES

from anonymizer.clean import anonymize
from anonymizer.detector import LABELS, Detector, GlinerDetector, chunk_words, split_words

MODEL_ID = "knowledgator/gliner-pii-base-v1.0"
MODEL_REVISION = "61726e0ad791dcab3e29339bbec3ad42ded65641"  # the commit the slow test was run against


class FakeModel:
    """Tags every occurrence of the given words as `person`, like GLiNER would: offsets in the text it is given."""

    def __init__(self, names):
        self.names = names
        self.texts = []

    def predict_entities(self, text, labels, threshold=0.5):
        self.texts.append(text)
        out = []
        for name in self.names:
            for m in re.finditer(re.escape(name), text):
                out.append({"start": m.start(), "end": m.end(), "text": m.group(), "label": "person", "score": 0.9})
        return out


def _detector(model, max_tokens=384, overlap=32):
    detector = object.__new__(GlinerDetector)  # no model download: the fake stands in for GLiNER
    detector.model = model
    detector.threshold = 0.5
    detector.max_tokens = max_tokens
    detector.overlap_words = overlap
    detector.labels = list(LABELS)
    detector._tokenizer = None
    detector._cost_cache = {}
    return detector


def test_labels_and_protocol():
    assert LABELS == {
        "person": "[PERSON]",
        "address": "[ADDRESS]",
        "phone number": "[PHONE]",
        "email": "[EMAIL]",
        "passport number": "[ID]",
        "national id number": "[ID]",
        "social security number": "[ID]",
        "tax id": "[ID]",
    }
    assert isinstance(_detector(FakeModel([])), Detector)


def test_split_words_matches_gliner_and_cuts_cjk():
    text = "Hello, Jean-Pierre! 我叫王伟。"
    assert [text[s:e] for s, e in split_words(text)] == ["Hello", ",", "Jean-Pierre", "!", "我", "叫", "王", "伟", "。"]


def test_chunk_words_respects_the_budget_and_overlaps():
    assert chunk_words([1] * 10, 4, 1) == [(0, 4), (3, 7), (6, 10)]
    assert chunk_words([5, 1, 1], 4, 1) == [(0, 1), (1, 3)]  # a word over the budget is a window of its own
    assert chunk_words([], 4, 1) == []
    for i, j in chunk_words([2, 3, 1, 4, 2, 2, 1, 3], 5, 2):
        assert j > i


def test_offsets_map_back_to_the_original_text():
    text = "Write  to   Ana Lima\tin Porto.\n王伟 lives here."
    detector = _detector(FakeModel(["Ana Lima", "王 伟"]))
    spans = detector.spans(text)
    assert sorted(text[s:e] for s, e, _ in spans) == ["Ana Lima", "王伟"]
    assert {m for _, _, m in spans} == {"[PERSON]"}


def test_long_text_is_windowed_and_a_name_on_a_boundary_is_found():
    filler = " ".join(f"word{i}" for i in range(1000))
    text = f"{filler} Ana Lima {filler} Ana Lima"
    model = FakeModel(["Ana Lima"])
    detector = _detector(model, max_tokens=100, overlap=8)
    spans = detector.spans(text)
    assert len(model.texts) > 20 and all(len(t.split()) <= 100 for t in model.texts)
    assert sum(1 for s, e, _ in set(spans) if text[s:e] == "Ana Lima") == 2
    assert anonymize(text, detector).count("[PERSON]") == 2


# --- the real model ---------------------------------------------------------------------------------------------

NAMES = {
    "en.txt": ["Margaret", "Thornbury"],
    "fr.txt": ["Élodie", "Marchand"],
    "de.txt": ["Jürgen", "Wohlfahrt"],
    "es.txt": ["Lucía", "Fernández", "Ortega"],
    "zh.txt": ["王晓明"],
    "ar.txt": ["ليلى", "الحسيني"],
    "ru.txt": ["Ольга", "Смирнова"],
}


@pytest.fixture(scope="module")
def real_detector():
    model_dir = os.environ.get("GLINER_MODEL_DIR")
    if not model_dir:
        from huggingface_hub import snapshot_download

        model_dir = snapshot_download(MODEL_ID, revision=MODEL_REVISION)
    return GlinerDetector(model_dir, threshold=float(os.environ.get("GLINER_THRESHOLD", "0.5")))


# Known misses at the pinned revision and threshold 0.5: the model's encoder is the English DeBERTa-v3 (the model card
# says "language: en"). On these sentences it scores the Chinese name below 0.35 and the Arabic name at 0.44, so they
# survive. Strict: the day a model or threshold change finds them, the test turns red and this list must go.
KNOWN_MISSES = {
    "zh.txt": "Chinese names score below the threshold with this English-backbone model",
    "ar.txt": "Arabic names score below the threshold with this English-backbone model",
}


@pytest.mark.slow
@pytest.mark.parametrize(
    "name",
    [
        pytest.param(n, marks=pytest.mark.xfail(reason=KNOWN_MISSES[n], strict=True)) if n in KNOWN_MISSES else n
        for n in sorted(NAMES)
    ],
)
def test_no_fixture_name_survives_the_real_model(real_detector, name):
    text = (FIXTURES / "pii" / name).read_text(encoding="utf-8").strip()
    out = anonymize(text, real_detector)
    assert [n for n in NAMES[name] if n in out] == [], out


@pytest.mark.slow
def test_mean_seconds_per_prompt(real_detector, capsys):
    files = sorted((FIXTURES / "pii").glob("*.txt"))
    assert {f.name for f in files} == set(NAMES)
    real_detector.spans("warm up")
    durations = []
    for path in files:
        text = path.read_text(encoding="utf-8").strip()
        start = time.perf_counter()
        anonymize(text, real_detector)
        durations.append(time.perf_counter() - start)
    mean = sum(durations) / len(durations)
    with capsys.disabled():
        print(f"\nGLiNER {MODEL_ID}@{MODEL_REVISION[:7]}: {mean:.3f} s per prompt (mean of {len(files)})")
    assert mean < 2.0
