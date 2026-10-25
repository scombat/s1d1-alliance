"""The batch job: a Cloud Run Job that Cloud Scheduler starts every 2 days (docs/31 of the project).

It lists the raw objects `raw/YYYY/MM/DD/<uuid>.ndjson` of RAW_BUCKET, leaves out those whose clean object
`clean/YYYY/MM/DD/<uuid>.ndjson` already exists in R2, draws at most MAX_PROMPTS lines uniformly at random from the
others (one draw over all their lines, not per object), cleans the drawn lines in a random order and writes, for
each raw object with at least one kept line, its clean object. It stops cleaning at MAX_SECONDS, so a slow model
bounds the cost instead of raising it, and writes what it has. A line that is not drawn is never stored: its raw
object is deleted at 3 days by the bucket's lifecycle rule, as before. A raw object with no line drawn has no clean
object, so a later run may draw from it while it still exists.

Run: `python -m anonymizer.batch`. Exit status 0 when the run went through (whatever it kept), 1 when it could not
(storage, every cleaned line failed, a configuration error), so that the job shows as failed.

The log lines carry counts and error class names only, never a prompt, a line or an object name.
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from anonymizer.app import AllLinesFailed, clean_key, gcs_reader, gliner_detector, log, r2_writer
from anonymizer.clean import DROPPED, UNCOVERED_SCRIPT, clean_line_reason, dumps_line
from anonymizer.detector import Detector

ListObjects = Callable[[str, str], Iterable[str]]
ReadObject = Callable[[str, str], bytes]
WriteObject = Callable[[str, bytes], None]
ExistsObject = Callable[[str], bool]

DEFAULT_MAX_PROMPTS = 5000
DEFAULT_MAX_SECONDS = 3300  # under the job's task timeout of 3600 s (terraform/gcp.tf), so the writes always happen


@dataclass
class Counts:
    objects: int = 0  # raw objects listed under raw/ with a valid name
    already_clean: int = 0  # left out: their clean object exists
    lines: int = 0  # non-empty lines of the objects read
    drawn: int = 0
    cleaned: int = 0  # drawn lines cleaned before the deadline
    kept: int = 0
    dropped: dict[str, int] = field(default_factory=lambda: {DROPPED: 0, UNCOVERED_SCRIPT: 0})
    failed: int = 0
    written: int = 0  # clean objects written
    deadline: bool = False

    def fields(self) -> dict:
        return {
            "objects": self.objects,
            "already_clean": self.already_clean,
            "lines": self.lines,
            "drawn": self.drawn,
            "cleaned": self.cleaned,
            "kept": self.kept,
            **self.dropped,
            "failed": self.failed,
            "written": self.written,
            "deadline_reached": self.deadline,
        }


def run(
    *,
    raw_bucket: str,
    list_objects: ListObjects,
    read_object: ReadObject,
    write_object: WriteObject,
    exists_object: ExistsObject,
    detector: Callable[[], Detector],
    max_prompts: int,
    max_seconds: float,
    rng: random.Random,
    clock: Callable[[], float] = time.monotonic,
) -> Counts:
    """One run. `detector` is called once, after the draw, and only when a line was drawn (the models load slowly).

    The draw is a reservoir of `max_prompts` lines (Algorithm R): the lines of every object read have the same chance,
    and memory holds `max_prompts` lines at most, however much was sent. Raises AllLinesFailed when every cleaned
    line raised (the detector itself is broken): nothing is written then.
    """
    start = clock()
    counts = Counts()
    reservoir: list[tuple[str, str]] = []  # (raw object name, line)
    for name in sorted(list_objects(raw_bucket, "raw/")):
        key = clean_key(name)
        if key is None:
            continue
        counts.objects += 1
        if exists_object(key):
            counts.already_clean += 1
            continue
        raw = read_object(raw_bucket, name)
        for line in raw.decode("utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            counts.lines += 1
            if len(reservoir) < max_prompts:
                reservoir.append((name, line))
            else:
                j = rng.randrange(counts.lines)
                if j < max_prompts:
                    reservoir[j] = (name, line)
    counts.drawn = len(reservoir)
    if not reservoir:
        return counts

    # The reservoir keeps the first lines in order: shuffle, so that a deadline cuts a random part, not the last days.
    rng.shuffle(reservoir)
    model = detector()
    out: dict[str, list[str]] = defaultdict(list)
    for name, line in reservoir:
        if clock() - start >= max_seconds:
            counts.deadline = True
            break
        counts.cleaned += 1
        try:
            parsed = json.loads(line)
        except ValueError:
            counts.dropped[DROPPED] += 1
            continue
        try:
            cleaned, reason = clean_line_reason(parsed, model)
        except Exception:  # noqa: BLE001 - counted, never logged with its message (it may quote the prompt)
            counts.failed += 1
            continue
        if cleaned is None:
            counts.dropped[reason or DROPPED] += 1
            continue
        out[name].append(dumps_line(cleaned) + "\n")
        counts.kept += 1
    if counts.cleaned and counts.failed == counts.cleaned:
        raise AllLinesFailed

    for name in sorted(out):
        write_object(clean_key(name), "".join(out[name]).encode("utf-8"))
        counts.written += 1
    return counts


# --- the default adapters ---------------------------------------------------------------------------------------


def gcs_lister() -> ListObjects:
    """Lists the object names of a bucket under a prefix, with the identity of the job (no key)."""
    from google.cloud import storage  # noqa: PLC0415

    client = storage.Client()

    def list_objects(bucket: str, prefix: str) -> Iterable[str]:
        return [blob.name for blob in client.list_blobs(bucket, prefix=prefix)]

    return list_objects


def r2_exists() -> ExistsObject:
    """True when the R2 object exists (HeadObject; the write token of the anonymizer allows it, cloudflare.tf)."""
    import boto3  # noqa: PLC0415
    from botocore.exceptions import ClientError  # noqa: PLC0415

    bucket = os.environ.get("R2_BUCKET", "s1d1-clean")
    client = boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )

    def exists(key: str) -> bool:
        try:
            client.head_object(Bucket=bucket, Key=key)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise
        return True

    return exists


def _positive(name: str, default: int) -> int:
    value = int(os.environ.get(name, str(default)))
    if value < 1:
        raise ValueError(name)
    return value


def main() -> int:
    raw_bucket = os.environ.get("RAW_BUCKET")
    if not raw_bucket:
        log("ERROR", "RAW_BUCKET is not set")
        return 1
    try:
        max_prompts = _positive("MAX_PROMPTS", DEFAULT_MAX_PROMPTS)
        max_seconds = _positive("MAX_SECONDS", DEFAULT_MAX_SECONDS)
    except ValueError as error:
        log("ERROR", "bad setting", setting=str(error))
        return 1
    started = time.monotonic()
    try:
        counts = run(
            raw_bucket=raw_bucket,
            list_objects=gcs_lister(),
            read_object=gcs_reader(),
            write_object=r2_writer(),
            exists_object=r2_exists(),
            detector=gliner_detector,
            max_prompts=max_prompts,
            max_seconds=max_seconds,
            rng=random.SystemRandom(),
        )
    except Exception as error:  # noqa: BLE001 - the class name only: a message may quote a prompt
        log("ERROR", "batch failed", error=type(error).__name__)
        return 1
    log(
        "INFO",
        "batch done",
        max_prompts=max_prompts,
        seconds=round(time.monotonic() - started, 1),
        **counts.fields(),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
