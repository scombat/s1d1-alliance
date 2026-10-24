"""The GLiNER adapter: word splitting, windows and offsets (fast, with a fake model), then the real model (slow)."""

from __future__ import annotations

import os
import re
import time

import pytest
from fakes import FIXTURES

from anonymizer.clean import anonymize, clean_line_reason
from anonymizer.coverage import COVERED_SCRIPTS, letter_scripts
from anonymizer.detector import (
    LABELS,
    MODELS,
    Detector,
    EnsembleDetector,
    GlinerDetector,
    chunk_words,
    gliner_ensemble,
    split_words,
)


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


def test_ensemble_unites_the_spans_of_its_members():
    a = _detector(FakeModel(["Ana"]))
    b = _detector(FakeModel(["Lima"]))
    text = "Ana Lima"
    assert sorted(EnsembleDetector([a, b]).spans(text)) == [(0, 3, "[PERSON]"), (4, 8, "[PERSON]")]
    assert anonymize(text, EnsembleDetector([a, b])) == "[PERSON] [PERSON]"
    with pytest.raises(ValueError):
        EnsembleDetector([])


def test_models_are_pinned_to_commits():
    assert set(MODELS) == {"knowledgator", "urchade"}
    assert all(re.fullmatch(r"[0-9a-f]{40}", revision) for _, revision in MODELS.values())


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


# --- the real models -------------------------------------------------------------------------------------------

# Per fixture: the names and a distinctive fragment of the address, none of which may survive.
EXPECTED = {
    "en.txt": ["Margaret", "Thornbury", "Willow Lane"],
    "fr.txt": ["Élodie", "Marchand", "Tanneurs"],
    "de.txt": ["Jürgen", "Wohlfahrt", "Lindenstraße"],
    "es.txt": ["Lucía", "Fernández", "Ortega", "calle Mayor"],
    "it.txt": ["Giulia", "Bianchi", "Garibaldi"],
    "pt.txt": ["João", "Pereira", "Rua Augusta"],
    "pl.txt": ["Katarzyna", "Nowak", "Marszałkowskiej"],
    "tr.txt": ["Mehmet", "Yılmaz", "İstiklal"],
    "vi.txt": ["Nguyễn Văn An", "Lý Thường Kiệt"],
    "el.txt": ["Γιώργο", "Παπαδόπουλο", "Ερμού"],
    "he.txt": ["דוד", "כהן", "הרצל"],
    "ar.txt": ["ليلى", "الحسيني", "الملك فهد"],
    "fa.txt": ["سارا", "محمدی", "ولیعصر"],
    "zh.txt": ["王晓明", "建国路"],
    "ko.txt": ["김민준", "테헤란로"],
    "ru.txt": ["Ольга", "Смирнова", "Ленина"],
    "uk.txt": ["Олена", "Коваленко", "Хрещатик"],
    "ja.txt": ["山田花子", "西新宿"],
    "hi.txt": ["राहुल", "शर्मा", "एमजी रोड"],
    "th.txt": ["สมชาย", "สุขุมวิท"],
}


def _fixture(name):
    return (FIXTURES / "pii" / name).read_text(encoding="utf-8").strip()


def _script(text):
    counts = letter_scripts(text)
    return max(counts, key=counts.get)


@pytest.fixture(scope="module")
def ensemble():
    dirs = [d for d in os.environ.get("GLINER_MODEL_DIRS", "").split(",") if d]
    if not dirs:
        from huggingface_hub import snapshot_download

        dirs = [snapshot_download(repo, revision=revision) for repo, revision in MODELS.values()]
    return gliner_ensemble(dirs, threshold=float(os.environ.get("GLINER_THRESHOLD", "0.5")))


def test_every_fixture_has_its_expectations_and_every_covered_script_a_fixture():
    files = {f.name for f in (FIXTURES / "pii").glob("*.txt")}
    assert files == set(EXPECTED)
    assert {_script(_fixture(n)) for n in files} >= COVERED_SCRIPTS


@pytest.mark.slow
@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_covered_scripts_lose_every_name_and_address(ensemble, name):
    text = _fixture(name)
    line = {"id": "x", "prompt": text, "source": "capture:direct", "terms_version": "v1"}
    cleaned, reason = clean_line_reason(line, ensemble)
    if _script(text) in COVERED_SCRIPTS:
        assert reason is None
        assert [n for n in EXPECTED[name] if n in cleaned["prompt"]] == [], cleaned["prompt"]
        assert "[PHONE]" in cleaned["prompt"]
    else:
        assert (cleaned, reason) == (None, "dropped_uncovered_script")


def _uncovered_scripts():
    return sorted({_script(_fixture(n)) for n in EXPECTED} - COVERED_SCRIPTS)


@pytest.mark.slow
@pytest.mark.parametrize("script", _uncovered_scripts())
def test_uncovered_scripts_are_really_missed(ensemble, script):
    # Why the script is dropped: in at least one of its fixtures the ensemble leaves a name or an address. The day it
    # leaves none, this test fails: add the script to COVERED_SCRIPTS and this case goes.
    survivors = {}
    for name in sorted(n for n in EXPECTED if _script(_fixture(n)) == script):
        out = anonymize(_fixture(name), ensemble)
        survivors[name] = [n for n in EXPECTED[name] if n in out]
    assert any(survivors.values()), survivors


@pytest.mark.slow
def test_mean_seconds_per_prompt(ensemble, capsys):
    ensemble.spans("warm up")
    durations = []
    for name in sorted(EXPECTED):
        text = _fixture(name)
        start = time.perf_counter()
        anonymize(text, ensemble)
        durations.append(time.perf_counter() - start)
    mean = sum(durations) / len(durations)
    with capsys.disabled():
        print(f"\nensemble {', '.join(MODELS)}: {mean:.3f} s per prompt (mean of {len(durations)} fixtures)")
    assert mean < 2.0


def test_pronouns_and_roles_are_not_persons():
    text = "I told you that Ana Lima and 你 met the user."
    detector = _detector(FakeModel(["I", "you", "Ana Lima", "你", "user"]))
    assert sorted(text[s:e] for s, e, _ in detector.spans(text)) == ["Ana Lima"]
