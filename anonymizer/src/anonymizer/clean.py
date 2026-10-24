"""anonymize() and clean_line(): from a raw line to a line of the clean corpus (decision 11). No I/O.

Every key of a clean line is constrained: the prompt is anonymised, the metadata must be one of the values tantive
emits (anything else becomes null), and a line whose source or terms version is not one of the known forms is
dropped. A prompt with a single letter in a script the anonymiser does not cover is dropped too (`coverage.py`).
"""

from __future__ import annotations

import datetime
import hashlib
import json
import math
import re

from anonymizer.coverage import is_uncovered
from anonymizer.detector import Detector
from anonymizer.patterns import find_patterns, merge_spans

__all__ = ["CLEAN_KEYS", "anonymize", "clean_line", "clean_line_reason", "dumps_line", "merge_spans"]

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

# The values tantive emits: the formats it serves, the native and LiteLLM tier sets (tiers.py), the 19 tasks (tasks.py).
FORMATS = frozenset({"systemone", "decisions", "chat", "classify", "predict"})
TIERS = frozenset({"TRIVIAL", "LOW", "MEDIUM", "HIGH", "EXPERT", "SIMPLE", "COMPLEX", "REASONING"})
TASKS = frozenset(
    {
        "information_seeking",
        "reasoning",
        "planning",
        "editing",
        "summarization",
        "extraction",
        "classification",
        "translation",
        "coding_debugging",
        "math",
        "data_analysis",
        "writing",
        "role_playing",
        "advice_seeking",
        "brainstorming",
        "completion",
        "action_execution",
        "chat",
        "other",
    }
)
_PROBABILITIES = ("pred_score", "pred_needs_tools", "pred_confidence")

_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_REVISION = re.compile(r"[0-9a-f]{7,40}")
_TERMS = re.compile(r"v[0-9]{1,3}")
_SOURCE = re.compile(r"capture:community|capture:direct|capture:selfhosted:" + _UUID.pattern)

# Why a line was dropped: the counter of the object's log line.
DROPPED = "dropped"
UNCOVERED_SCRIPT = "dropped_uncovered_script"


def anonymize(text: str, detector: Detector) -> str:
    """The text with every span of the patterns and of the detector replaced by its marker (right to left)."""
    spans = find_patterns(text) + list(detector.spans(text))
    out = text
    for start, end, marker in reversed(merge_spans(spans, len(text))):
        out = out[:start] + marker + out[end:]
    return out


def _day(raw: dict) -> str | None:
    """The first of `time` and `received` that starts with a real calendar date (2026-02-30 is not one)."""
    for key in ("time", "received"):
        value = raw.get(key)
        if isinstance(value, str) and _DAY.match(value):
            try:
                return datetime.date.fromisoformat(value[:10]).isoformat()
            except ValueError:
                continue
    return None


def _clean_id(value: object) -> str | None:
    """A UUID (in lower case); any other non-empty string (a sender may put anything there) as the first 32 hex of
    its SHA-256; None for no usable id (absent, empty, not a string), never a hash of "null"."""
    if not isinstance(value, str) or not value:
        return None
    if _UUID.fullmatch(value.lower()):
        return value.lower()
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def _probability(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    value = float(value)
    return value if math.isfinite(value) and 0.0 <= value <= 1.0 else None


def _member(value: object, allowed: frozenset[str]) -> str | None:
    return value if isinstance(value, str) and value in allowed else None


def _matching(value: object, pattern: re.Pattern[str]) -> str | None:
    return value if isinstance(value, str) and pattern.fullmatch(value) else None


def clean_line_reason(raw: object, detector: Detector) -> tuple[dict | None, str | None]:
    """The clean line and None, or None and the reason it was dropped (DROPPED or UNCOVERED_SCRIPT)."""
    if not isinstance(raw, dict):
        return None, DROPPED
    prompt = raw.get("prompt")
    if not isinstance(prompt, str) or not prompt:
        return None, DROPPED
    source = _matching(raw.get("source"), _SOURCE)
    terms = _matching(raw.get("terms_version"), _TERMS)
    line_id = _clean_id(raw.get("id"))
    if source is None or terms is None or line_id is None:
        return None, DROPPED
    if is_uncovered(prompt):
        return None, UNCOVERED_SCRIPT
    values = {
        "id": line_id,
        "time": _day(raw),
        "prompt": anonymize(prompt, detector),
        "source": source,
        "model_revision": _matching(raw.get("model_revision"), _REVISION),
        "terms_version": terms,
        "format": _member(raw.get("format"), FORMATS),
        "pred_tier": _member(raw.get("pred_tier"), TIERS),
        "pred_task": _member(raw.get("pred_task"), TASKS),
        **{key: _probability(raw.get(key)) for key in _PROBABILITIES},
    }
    return {key: values[key] for key in CLEAN_KEYS}, None


def clean_line(raw: object, detector: Detector) -> dict | None:
    """The decision-11 line of a raw line, or None when it is dropped.

    Every key is present, in the order of CLEAN_KEYS. `time` is the capture day; without a usable `time` (a real
    calendar date), the day the Worker received the line (`received`); null when neither is there. `id` is kept when
    it is a UUID (lower-cased), otherwise replaced by the first 32 hex of its SHA-256. `format`, `pred_tier` and
    `pred_task` must be values tantive emits, `model_revision` a hex revision, the `pred_*` numbers probabilities in
    [0, 1]: otherwise null. The line is dropped without a prompt, without a string id, without a known source
    (`capture:community`, `capture:direct`, `capture:selfhosted:<lower-case uuid>`) or terms version (`v` and 1 to 3
    digits), or when its prompt has a single letter in a script the anonymiser does not cover.
    """
    return clean_line_reason(raw, detector)[0]


def dumps_line(line: dict) -> str:
    """One NDJSON line (no newline): UTF-8 as is, the default separators."""
    return json.dumps(line, ensure_ascii=False)
