"""The Eventarc handler: a raw ASGI app, with the storage reader, the R2 writer and the detector injected."""

from __future__ import annotations

import asyncio
import json

from fakes import FakeDetector, NoDetector, clean_expected_text, raw_valid_lines

from anonymizer.app import create_app

UUID = "0b7f3c1e-5a2d-4c8e-9f10-2a3b4c5d6e7f"
RAW = f"raw/2026/10/24/{UUID}.ndjson"
CLEAN = f"clean/2026/10/24/{UUID}.ndjson"
SENTINEL = "Zéphyrine Okonkwo-Villeneuve lives at 12 rue des Lilas"


class FakeStore:
    def __init__(self, objects=None, fail_read=False, fail_write=False):
        self.objects = dict(objects or {})
        self.reads = []
        self.writes = {}
        self.fail_read = fail_read
        self.fail_write = fail_write

    def read(self, bucket, name):
        self.reads.append((bucket, name))
        if self.fail_read:
            raise RuntimeError(f"read failed for a body that holds {SENTINEL}")
        return self.objects[(bucket, name)]

    def write(self, key, body):
        if self.fail_write:
            raise RuntimeError(f"write failed: {SENTINEL}")
        self.writes[key] = body


def _raw_object(lines):
    return ("\n".join(json.dumps(line) for line in lines) + "\n").encode()


def _app(store, detector=None, raw_bucket="s1d1-raw"):
    detector = detector or NoDetector()
    return create_app(
        read_object=store.read, write_object=store.write, detector_factory=lambda: detector, raw_bucket=raw_bucket
    )


def _call(app, body, method="POST", path="/", headers=None):
    if isinstance(body, dict):
        body = json.dumps(body).encode()
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [(k.encode(), v.encode()) for k, v in (headers or {}).items()],
    }
    chunks = [{"type": "http.request", "body": body, "more_body": False}]
    sent = []

    async def receive():
        return chunks.pop(0) if chunks else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], payload


def _event(name, bucket="s1d1-raw"):
    return {"bucket": bucket, "name": name, "contentType": "application/x-ndjson", "size": "123"}


def test_event_outside_raw_is_ignored():
    store = FakeStore()
    for name in ("other/a", "clean/2026/10/24/x.ndjson", "raw/notes.txt", "raw/2026/10/24/not-a-uuid.ndjson"):
        status, _ = _call(_app(store), _event(name))
        assert status == 204, name
    assert store.reads == [] and store.writes == {}


def test_an_object_is_written_under_the_same_uuid():
    store = FakeStore({("s1d1-raw", RAW): _raw_object(raw_valid_lines())})
    status, _ = _call(_app(store), _event(RAW), headers={"ce-type": "google.cloud.storage.object.v1.finalized"})
    assert status == 204
    assert store.reads == [("s1d1-raw", RAW)]
    assert list(store.writes) == [CLEAN]
    assert store.writes[CLEAN].decode() == clean_expected_text()


def test_a_second_delivery_rewrites_the_same_key():
    store = FakeStore({("s1d1-raw", RAW): _raw_object(raw_valid_lines())})
    app = _app(store)
    assert _call(app, _event(RAW))[0] == 204
    first = store.writes[CLEAN]
    assert _call(app, _event(RAW))[0] == 204
    assert list(store.writes) == [CLEAN] and store.writes[CLEAN] == first


def test_bad_lines_in_the_object_are_skipped():
    lines = raw_valid_lines()
    body = (
        _raw_object(lines[:1]) + b"{not json\n\n" + _raw_object([{"id": "x", "source": "capture:direct"}] + lines[1:])
    )
    store = FakeStore({("s1d1-raw", RAW): body})
    assert _call(_app(store), _event(RAW))[0] == 204
    assert store.writes[CLEAN].decode() == clean_expected_text()


def test_an_object_without_a_good_line_writes_nothing():
    store = FakeStore({("s1d1-raw", RAW): b"{bad\n"})
    assert _call(_app(store), _event(RAW))[0] == 204
    assert store.writes == {}


def test_a_storage_error_is_500():
    assert _call(_app(FakeStore(fail_read=True)), _event(RAW))[0] == 500
    store = FakeStore({("s1d1-raw", RAW): _raw_object(raw_valid_lines())}, fail_write=True)
    assert _call(_app(store), _event(RAW))[0] == 500


def test_a_detector_error_is_500():
    class Broken:
        def spans(self, text):
            raise ValueError(f"model failed on {text}")

    store = FakeStore({("s1d1-raw", RAW): _raw_object(raw_valid_lines())})
    assert _call(_app(store, Broken()), _event(RAW))[0] == 500
    assert store.writes == {}


def test_the_detector_is_built_once_and_lazily():
    built = []

    def factory():
        built.append(1)
        return NoDetector()

    store = FakeStore({("s1d1-raw", RAW): _raw_object(raw_valid_lines())})
    app = create_app(read_object=store.read, write_object=store.write, detector_factory=factory, raw_bucket="s1d1-raw")
    assert _call(app, _event("other/a"))[0] == 204
    assert built == []
    _call(app, _event(RAW))
    _call(app, _event(RAW))
    assert built == [1]


def test_requests_that_are_not_events():
    store = FakeStore()
    app = _app(store)
    assert _call(app, b"", method="GET")[0] == 405
    assert _call(app, _event(RAW), path="/other")[0] == 404
    assert store.reads == []


def test_an_event_that_can_never_succeed_is_acknowledged(capsys):
    # Eventarc retries any answer that is not 2xx: these would be retried forever
    store = FakeStore()
    app = _app(store)
    for body in (b"{not json", b"[1, 2]", json.dumps({"bucket": "b"}).encode(), b"x" * 1_048_577):
        assert _call(app, body)[0] == 204
    other_type = {"ce-type": "google.cloud.storage.object.v1.deleted"}
    assert _call(app, _event(RAW), headers=other_type)[0] == 204
    assert store.reads == [] and store.writes == {}
    assert "not json" not in capsys.readouterr().err


def test_only_the_raw_bucket_is_read(capsys, monkeypatch):
    store = FakeStore({("other-bucket", RAW): _raw_object(raw_valid_lines())})
    assert _call(_app(store), _event(RAW, bucket="other-bucket"))[0] == 204
    assert store.reads == [] and store.writes == {}
    assert "other-bucket" not in capsys.readouterr().err
    # the bucket comes from RAW_BUCKET by default, and without it nothing is read (500: a configuration error)
    store = FakeStore({("s1d1-raw", RAW): _raw_object(raw_valid_lines())})
    app = create_app(read_object=store.read, write_object=store.write, detector_factory=NoDetector)
    monkeypatch.delenv("RAW_BUCKET", raising=False)
    assert _call(app, _event(RAW))[0] == 500
    assert _call(app, _event("other/a"))[0] == 204  # an object outside raw/ needs no configuration
    monkeypatch.setenv("RAW_BUCKET", "s1d1-raw")
    assert _call(app, _event(RAW))[0] == 204
    assert list(store.writes) == [CLEAN]


def test_one_failing_line_is_dropped_not_the_object(capsys):
    class FailsOnOne:
        def spans(self, text):
            if "meeting" in text:
                raise ValueError(f"model failed on {text}")
            return []

    store = FakeStore({("s1d1-raw", RAW): _raw_object(raw_valid_lines())})
    assert _call(_app(store, FailsOnOne()), _event(RAW))[0] == 204
    assert len(store.writes[CLEAN].decode().splitlines()) == 2
    logs = capsys.readouterr().err
    assert '"failed": 1' in logs and '"kept": 2' in logs and "meeting" not in logs


def test_uncovered_scripts_are_dropped_and_counted(capsys):
    lines = raw_valid_lines()
    lines[1] = {**lines[1], "prompt": "Меня зовут Ольга Смирнова, я живу в Казани."}
    store = FakeStore({("s1d1-raw", RAW): _raw_object(lines)})
    assert _call(_app(store), _event(RAW))[0] == 204
    assert len(store.writes[CLEAN].decode().splitlines()) == 2
    logs = capsys.readouterr().err
    assert '"dropped_uncovered_script": 1' in logs and "Ольга" not in logs


def test_lifespan_is_answered():
    app = _app(FakeStore())
    messages = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
    sent = []

    async def receive():
        return messages.pop(0)

    async def send(message):
        sent.append(message["type"])

    asyncio.run(app({"type": "lifespan"}, receive, send))
    assert sent == ["lifespan.startup.complete", "lifespan.shutdown.complete"]


def test_no_prompt_text_in_logs(capsys):
    lines = [{**line, "prompt": f"{SENTINEL}, call +33 6 12 34 56 78"} for line in raw_valid_lines()]
    detector = FakeDetector([(0, 28, "[PERSON]")])
    store = FakeStore({("s1d1-raw", RAW): _raw_object(lines)})
    assert _call(_app(store, detector), _event(RAW))[0] == 204
    assert _call(_app(FakeStore(fail_read=True)), _event(RAW))[0] == 500
    failing = FakeStore({("s1d1-raw", RAW): _raw_object(lines)}, fail_write=True)
    assert _call(_app(failing, detector), _event(RAW))[0] == 500
    out, err = capsys.readouterr()
    logs = out + err
    assert RAW in logs  # object names are logged
    for fragment in ("Zéphyrine", "Okonkwo", "Lilas", "12 34 56 78"):
        assert fragment not in logs
    clean = store.writes[CLEAN].decode()
    assert "Zéphyrine" not in clean and "[PERSON]" in clean and "[PHONE]" in clean
