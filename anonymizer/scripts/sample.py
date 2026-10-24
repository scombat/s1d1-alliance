"""Run the real detectors on the invented test fixtures and write examples/before-after.md.

    GLINER_MODEL_DIRS=/model/knowledgator,/model/urchade uv run --extra model python scripts/sample.py

Every text comes from tests/fixtures (invented sentences, never a real prompt). A prompt with a letter in a script
the anonymiser does not cover is not anonymised but dropped, as the service does (src/anonymizer/coverage.py).
"""

from __future__ import annotations

import sys
from pathlib import Path

from anonymizer.app import gliner_detector
from anonymizer.clean import anonymize
from anonymizer.coverage import is_uncovered

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
COVERED = ["en", "fr", "de", "es", "el", "he", "ar", "zh", "ko"]
DROPPED = ["ru", "ja", "hi"]
PATTERN_MARKERS = ["[EMAIL]", "[PHONE]", "[IBAN]", "[CARD]", "[IP]", "[URL_CREDENTIALS]", "[SECRET]", "[ID]"]


def fixture(lang: str) -> str:
    return (FIXTURES / "pii" / f"{lang}.txt").read_text(encoding="utf-8").strip()


def pattern_examples() -> list[str]:
    """The first fixture line of each marker (the lines hold one marker each, or several for a mixed case)."""
    found: dict[str, str] = {}
    for line in (FIXTURES / "patterns.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or "\t" not in line:
            continue
        expected, text = line.split("\t", 1)
        if "drive.google.com" in text:  # a URL of a real service, even with a made-up id: use another [SECRET] case
            continue
        if expected in PATTERN_MARKERS and expected not in found:
            found[expected] = text
    return [found[m] for m in PATTERN_MARKERS if m in found]


def row(text: str, detector) -> str:
    cell = lambda s: "`" + s.replace("|", "\\|") + "`"  # noqa: E731
    if is_uncovered(text):
        return f"| {cell(text)} | **dropped**, not stored (a script the anonymiser does not cover) |"
    return f"| {cell(text)} | {cell(anonymize(text, detector))} |"


def main() -> None:
    detector = gliner_detector()
    out = [
        "# Before and after",
        "",
        "Produced by `scripts/sample.py` with the two models of the image and the patterns. Every text is an invented",
        "sentence from `tests/fixtures`, never a real prompt. Names and addresses come from the models; emails,",
        "phones, IBANs, cards, IPs and secrets from the validated patterns. Where an email span and a",
        "`[URL_CREDENTIALS]` span overlap, the longer one wins: the `user:password@host` of a URL is marked `[EMAIL]`,",
        "and the credentials are masked either way.",
        "",
        "## Covered scripts: masked",
        "",
        "| before | after |",
        "|---|---|",
        *(row(fixture(lang), detector) for lang in COVERED),
        "",
        "## Patterns",
        "",
        "| before | after |",
        "|---|---|",
        *(row(text, detector) for text in pattern_examples()),
        "",
        "## Uncovered scripts: the whole prompt is dropped",
        "",
        "A single letter in a script that the fixtures show the models miss (here Cyrillic, Japanese and Devanagari)",
        "drops the prompt: it is not written to the clean corpus at all.",
        "",
        "| before | after |",
        "|---|---|",
        *(row(fixture(lang), detector) for lang in DROPPED),
        row("Please translate this address for me: Ольга Смирнова, ул. Ленина, д. 12", detector),
        "",
    ]
    (ROOT / "examples").mkdir(exist_ok=True)
    (ROOT / "examples" / "before-after.md").write_text("\n".join(out), encoding="utf-8")
    print("examples/before-after.md written", file=sys.stderr)


if __name__ == "__main__":
    main()
