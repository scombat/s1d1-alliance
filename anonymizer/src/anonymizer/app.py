"""The Eventarc handler: a raw ASGI application, no framework.

Eventarc delivers the Cloud Storage "object finalized" event as a CloudEvent in binary mode: a `POST /` whose JSON
body is the storage object (at least `bucket` and `name`) and whose `ce-type` header names the event. For an object
`raw/YYYY/MM/DD/<uuid>.ndjson`, the handler reads it, cleans each line and writes
`clean/YYYY/MM/DD/<uuid>.ndjson` to R2, then answers 204. A rewrite replaces the object, so an event delivered
twice gives one clean object. Only objects of the bucket named by RAW_BUCKET are read. Any other object or bucket,
and any event that can never succeed (malformed, too large, another type), is acknowledged (204) and left alone,
since Eventarc retries every other answer. A failure that may pass (storage, a broken model) answers 500, and
Eventarc delivers the event again later.

The log lines carry object names, counts and error class names, never a prompt, a line or an error message.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import threading
from collections.abc import Awaitable, Callable
from typing import Any

from anonymizer.clean import DROPPED, UNCOVERED_SCRIPT, clean_line_reason, dumps_line
from anonymizer.detector import Detector

ReadObject = Callable[[str, str], bytes]
WriteObject = Callable[[str, bytes], None]

RAW_NAME = re.compile(
    r"raw/([0-9]{4}/[0-9]{2}/[0-9]{2})/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.ndjson"
)
FINALIZED = "google.cloud.storage.object.v1.finalized"
MAX_EVENT_BYTES = 1_048_576


def clean_key(raw_name: str) -> str | None:
    """clean/YYYY/MM/DD/<uuid>.ndjson for raw/YYYY/MM/DD/<uuid>.ndjson, None for any other name."""
    m = RAW_NAME.fullmatch(raw_name)
    return f"clean/{m.group(1)}/{m.group(2)}.ndjson" if m else None


def log(severity: str, message: str, **fields: Any) -> None:
    """One JSON line on stderr, which Cloud Run sends to Cloud Logging with its severity."""
    print(json.dumps({"severity": severity, "message": message, **fields}), file=sys.stderr, flush=True)


# --- the default adapters (imported lazily: the tests inject fakes) ---------------------------------------------


def gcs_reader() -> ReadObject:
    """Reads the raw bucket with the identity of the Cloud Run service (no key)."""
    from google.cloud import storage  # noqa: PLC0415

    client = storage.Client()

    def read(bucket: str, name: str) -> bytes:
        return client.bucket(bucket).blob(name).download_as_bytes()

    return read


def r2_writer() -> WriteObject:
    """Writes to the R2 bucket through its S3 API (R2_ENDPOINT, R2_BUCKET, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY)."""
    import boto3  # noqa: PLC0415

    bucket = os.environ.get("R2_BUCKET", "s1d1-clean")
    client = boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )

    def write(key: str, body: bytes) -> None:
        client.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/x-ndjson")

    return write


DEFAULT_MODEL_DIRS = "/model/knowledgator,/model/urchade"


def gliner_detector() -> Detector:
    """The ensemble of the models in GLINER_MODEL_DIRS (comma-separated), at GLINER_THRESHOLD."""
    from anonymizer.detector import gliner_ensemble  # noqa: PLC0415

    dirs = [d.strip() for d in os.environ.get("GLINER_MODEL_DIRS", DEFAULT_MODEL_DIRS).split(",") if d.strip()]
    return gliner_ensemble(dirs, float(os.environ.get("GLINER_THRESHOLD", "0.5")))


class _Lazy:
    """A value built on first use, once, by its factory."""

    def __init__(self, factory: Callable[[], Any]) -> None:
        self._factory = factory
        self._value: Any = None
        self._built = False
        self._lock = threading.Lock()

    def get(self) -> Any:
        if not self._built:
            with self._lock:
                if not self._built:
                    self._value = self._factory()
                    self._built = True
        return self._value


# --- the work ---------------------------------------------------------------------------------------------------


class AllLinesFailed(RuntimeError):
    """Every line of an object raised: the detector itself is broken, so the event must be retried."""


def process_object(raw: bytes, detector: Detector) -> tuple[bytes, dict[str, int]]:
    """The clean NDJSON of a raw object, with its counts: kept, dropped, dropped_uncovered_script, failed.

    A line that raises is dropped and counted as failed, so that one bad line cannot make the object fail forever;
    when every line raises, the object fails (AllLinesFailed) and Eventarc retries it.
    """
    out: list[str] = []
    counts = {"kept": 0, DROPPED: 0, UNCOVERED_SCRIPT: 0, "failed": 0}
    lines = [line for line in raw.decode("utf-8", errors="replace").splitlines() if line.strip()]
    for line in lines:
        try:
            parsed = json.loads(line)
        except ValueError:
            counts[DROPPED] += 1
            continue
        try:
            cleaned, reason = clean_line_reason(parsed, detector)
        except Exception:  # noqa: BLE001 - counted, never logged with its message (it may quote the prompt)
            counts["failed"] += 1
            continue
        if cleaned is None:
            counts[reason or DROPPED] += 1
            continue
        out.append(dumps_line(cleaned) + "\n")
        counts["kept"] += 1
    if lines and counts["failed"] == len(lines):
        raise AllLinesFailed
    return "".join(out).encode("utf-8"), counts


# --- ASGI -------------------------------------------------------------------------------------------------------

Send = Callable[[dict], Awaitable[None]]
Receive = Callable[[], Awaitable[dict]]


async def _respond(send: Send, status: int, body: bytes = b"") -> None:
    headers = [(b"content-length", str(len(body)).encode())]
    if body:
        headers.append((b"content-type", b"application/json"))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


async def _read_body(receive: Receive) -> bytes | None:
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return None
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > MAX_EVENT_BYTES:
            return None
        chunks.append(chunk)
        if not message.get("more_body", False):
            return b"".join(chunks)


def create_app(
    *,
    read_object: ReadObject | None = None,
    write_object: WriteObject | None = None,
    detector_factory: Callable[[], Detector] | None = None,
    raw_bucket: str | None = None,
) -> Callable[[dict, Receive, Send], Awaitable[None]]:
    """The ASGI application. Each dependency defaults to the real adapter, built on first use.

    `raw_bucket` (default: the RAW_BUCKET variable, required) is the only bucket whose objects are read: an event
    naming another bucket is acknowledged and ignored, so the service cannot be made to copy another bucket.
    """
    reader = _Lazy(lambda: read_object or gcs_reader())
    writer = _Lazy(lambda: write_object or r2_writer())
    detector = _Lazy(detector_factory or gliner_detector)

    def handle_object(bucket: str, name: str, key: str) -> None:
        raw = reader.get()(bucket, name)
        body, counts = process_object(raw, detector.get())
        if counts["kept"]:
            writer.get()(key, body)
        log("INFO", "cleaned", object=name, clean=key if counts["kept"] else None, **counts)

    async def app(scope: dict, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            return
        if scope.get("path", "/") != "/":
            await _respond(send, 404)
            return
        if scope.get("method") != "POST":
            await _respond(send, 405)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        # Eventarc retries any answer that is not 2xx: an event that can never succeed is acknowledged (204) with a
        # warning that quotes nothing from it.
        body = await _read_body(receive)
        if body is None:
            log("WARNING", "event body too large")
            await _respond(send, 204)
            return
        if headers.get("ce-type", FINALIZED) != FINALIZED:
            log("INFO", "ignored event type")
            await _respond(send, 204)
            return
        try:
            event = json.loads(body)
        except ValueError:
            event = None
        if (
            not isinstance(event, dict)
            or not isinstance(event.get("bucket"), str)
            or not isinstance(event.get("name"), str)
        ):
            log("WARNING", "not a storage event")
            await _respond(send, 204)
            return
        bucket, name = event["bucket"], event["name"]
        key = clean_key(name)
        if key is None:
            log("INFO", "ignored object")
            await _respond(send, 204)
            return
        expected = raw_bucket if raw_bucket is not None else os.environ.get("RAW_BUCKET")
        if not expected:
            log("ERROR", "RAW_BUCKET is not set")  # a configuration error: 500, retried until it is fixed
            await _respond(send, 500)
            return
        if bucket != expected:
            log("WARNING", "event from another bucket")
            await _respond(send, 204)
            return
        try:
            await asyncio.to_thread(handle_object, bucket, name, key)
        except Exception as error:  # noqa: BLE001 - any failure is a 500, so that Eventarc retries
            log("ERROR", "clean failed", object=name, error=type(error).__name__)
            await _respond(send, 500)
            return
        await _respond(send, 204)

    return app


app = create_app()
