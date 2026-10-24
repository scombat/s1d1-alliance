"""anonymize() and clean_line(): from a raw line to a line of the clean corpus (decision 11). No I/O."""

from __future__ import annotations

import json
import re

from anonymizer.detector import Detector, Span
from anonymizer.patterns import find_patterns

# The keys of a clean line, in this order (decision 11). Never instance_id, the IP address or the headers.
CLEAN_KEYS = (
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
)
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


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


def anonymize(text: str, detector: Detector) -> str:
    """The text with every span of the patterns and of the detector replaced by its marker (right to left)."""
    spans = find_patterns(text) + list(detector.spans(text))
    out = text
    for start, end, marker in reversed(merge_spans(spans, len(text))):
        out = out[:start] + marker + out[end:]
    return out


def _day(raw: dict) -> str | None:
    for key in ("time", "received"):
        value = raw.get(key)
        if isinstance(value, str) and _DAY.match(value):
            return value[:10]
    return None


def clean_line(raw: dict, detector: Detector) -> dict | None:
    """The decision-11 line of a raw line, prompt anonymised and time cut to the day; None without a prompt.

    `time` is the capture day; when the line has no usable `time`, the day the Worker received it (`received`).
    Absent or null optional keys are left out.
    """
    if not isinstance(raw, dict):
        return None
    prompt = raw.get("prompt")
    if not isinstance(prompt, str) or not prompt:
        return None
    out: dict = {}
    for key in CLEAN_KEYS:
        if key == "prompt":
            out[key] = anonymize(prompt, detector)
        elif key == "time":
            day = _day(raw)
            if day is not None:
                out[key] = day
        elif raw.get(key) is not None:
            out[key] = raw[key]
    return out


def dumps_line(line: dict) -> str:
    """One NDJSON line (no newline): UTF-8 as is, the default separators."""
    return json.dumps(line, ensure_ascii=False)
