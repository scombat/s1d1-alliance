"""Test doubles and fixture readers shared by the fast tests. No network, no model."""

from __future__ import annotations

import json
from pathlib import Path

CONTRACT = Path(__file__).resolve().parents[2] / "contract" / "fixtures"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


class FakeDetector:
    """Returns the spans it was given, whatever the text. Counts its calls."""

    def __init__(self, spans=None):
        self._spans = list(spans or [])
        self.calls = 0

    def spans(self, text):
        self.calls += 1
        return list(self._spans)


class NoDetector(FakeDetector):
    """A model that finds nothing: only the patterns act."""


def raw_valid_lines(received: str = "2026-10-24") -> list[dict]:
    """contract/fixtures/valid.ndjson as the ingest Worker stores it: `source` and `received` added.

    The tests of the Worker send the file with the community token, so line 1 is the community case, line 2
    carries a self-hosted UUID and line 3 is direct.
    """
    lines = [json.loads(line) for line in (CONTRACT / "valid.ndjson").read_text().splitlines() if line.strip()]
    sources = ["capture:community", f"capture:selfhosted:{lines[1]['instance_id']}", "capture:direct"]
    return [{**line, "source": source, "received": received} for line, source in zip(lines, sources, strict=True)]


def clean_expected_text() -> str:
    return (CONTRACT / "clean-expected.ndjson").read_text()
