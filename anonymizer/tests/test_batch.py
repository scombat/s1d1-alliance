"""The batch job: the draw, the deadline, the clean objects, the logs. Storage, R2 and the detector are fakes."""

from __future__ import annotations

import json
import random

import pytest
from fakes import FakeDetector, NoDetector, clean_expected_text, raw_valid_lines

from anonymizer import batch
from anonymizer.app import AllLinesFailed

BUCKET = "s1d1-raw"
UUIDS = [f"0b7f3c1e-5a2d-4c8e-9f10-2a3b4c5d6e{i:02x}" for i in range(16)]
SENTINEL = "Zéphyrine Okonkwo-Villeneuve lives at 12 rue des Lilas"


def raw_name(i: int, day: str = "2026/10/24") -> str:
    return f"raw/{day}/{UUIDS[i]}.ndjson"


def clean_name(i: int, day: str = "2026/10/24") -> str:
    return f"clean/{day}/{UUIDS[i]}.ndjson"


def raw_object(lines: list[dict]) -> bytes:
    return ("\n".join(json.dumps(line) for line in lines) + "\n").encode()


def numbered(n: int, start: int = 0) -> list[dict]:
    """n valid lines whose prompts are distinct and hold nothing to mask."""
    base = raw_valid_lines()[2]
    return [{**base, "id": f"line-{start + k}", "prompt": f"Explain topic number {start + k} in plain words."} for k in range(n)]


class Store:
    def __init__(self, objects: dict[str, bytes], clean: set[str] | None = None):
        self.objects = objects
        self.clean = set(clean or ())
        self.writes: dict[str, bytes] = {}
        self.reads: list[str] = []

    def list(self, bucket, prefix):
        assert bucket == BUCKET and prefix == "raw/"
        return list(self.objects)

    def read(self, bucket, name):
        assert bucket == BUCKET
        self.reads.append(name)
        return self.objects[name]

    def write(self, key, body):
        self.writes[key] = body

    def exists(self, key):
        return key in self.clean or key in self.writes


def go(store, *, detector=None, max_prompts=100, max_seconds=60, seed=0, clock=None):
    built = []

    def factory():
        built.append(1)
        return detector or NoDetector()

    counts = batch.run(
        raw_bucket=BUCKET,
        list_objects=store.list,
        read_object=store.read,
        write_object=store.write,
        exists_object=store.exists,
        detector=factory,
        max_prompts=max_prompts,
        max_seconds=max_seconds,
        rng=random.Random(seed),
        **({"clock": clock} if clock else {}),
    )
    return counts, built


def ids(body: bytes) -> list[int]:
    """The numbers of the kept lines of `numbered`, read back from their prompts."""
    return [int(json.loads(line)["prompt"].split()[3]) for line in body.decode().splitlines()]


def test_under_the_cap_every_line_is_cleaned_and_matches_the_contract():
    store = Store({raw_name(0): raw_object(raw_valid_lines())})
    counts, _ = go(store)
    # the lines come out in the order of the draw: the same lines as the contract's expected clean object
    assert list(store.writes) == [clean_name(0)]
    assert sorted(store.writes[clean_name(0)].decode().splitlines()) == sorted(clean_expected_text().splitlines())
    assert (counts.objects, counts.lines, counts.drawn, counts.kept, counts.written) == (1, 3, 3, 3, 1)


def test_the_draw_takes_max_prompts_lines_over_all_objects():
    store = Store({raw_name(i): raw_object(numbered(50, 50 * i)) for i in range(4)})
    counts, _ = go(store, max_prompts=40)
    assert counts.lines == 200 and counts.drawn == 40 and counts.kept == 40
    kept = [i for body in store.writes.values() for i in ids(body)]
    assert len(kept) == 40 and len(set(kept)) == 40
    # each clean object holds lines of its own raw object only
    for i in range(4):
        body = store.writes.get(clean_name(i))
        if body:
            assert all(50 * i <= x < 50 * (i + 1) for x in ids(body))


def test_the_draw_is_uniform_across_objects_and_positions():
    hits = [0] * 200
    for seed in range(300):
        store = Store({raw_name(i): raw_object(numbered(50, 50 * i)) for i in range(4)})
        go(store, max_prompts=20, seed=seed)
        for body in store.writes.values():
            for x in ids(body):
                hits[x] += 1
    # expected 300 * 20 / 200 = 30 per line; each quarter (one object) gets about a quarter of the draws
    quarters = [sum(hits[50 * q : 50 * (q + 1)]) for q in range(4)]
    assert all(1300 < q < 1700 for q in quarters), quarters
    assert sum(hits[:100]) == pytest.approx(sum(hits[100:]), rel=0.1)


def test_an_object_already_clean_is_not_read_again():
    store = Store({raw_name(0): raw_object(numbered(3)), raw_name(1): raw_object(numbered(3, 3))}, clean={clean_name(0)})
    counts, _ = go(store)
    assert store.reads == [raw_name(1)]
    assert list(store.writes) == [clean_name(1)]
    assert counts.already_clean == 1


def test_other_names_are_ignored():
    store = Store({"raw/2026/10/24/not-a-uuid.ndjson": b"{}\n", "raw/x.ndjson": b"{}\n", raw_name(0): raw_object(numbered(1))})
    counts, _ = go(store)
    assert counts.objects == 1 and store.reads == [raw_name(0)]


def test_nothing_to_do_loads_no_model_and_writes_nothing():
    counts, built = go(Store({}))
    assert built == [] and counts.drawn == 0
    counts, built = go(Store({raw_name(0): b"\n\n"}))
    assert built == [] and counts.lines == 0


def test_the_deadline_stops_the_cleaning_and_what_was_kept_is_written():
    ticks = iter(range(1000))
    store = Store({raw_name(0): raw_object(numbered(30))})
    # one tick at the start, then one per line: the 11th check is at 10 s
    counts, _ = go(store, max_seconds=10, clock=lambda: next(ticks))
    assert counts.deadline is True
    assert counts.cleaned == 9 and counts.kept == 9
    assert len(ids(store.writes[clean_name(0)])) == 9


def test_dropped_lines_are_counted_by_reason_and_not_written():
    lines = [*numbered(2), {"v": 1}, {**numbered(1, 9)[0], "prompt": "Привет, как дела?"}]
    body = raw_object(lines) + b"not json\n"
    counts, _ = go(Store({raw_name(0): body}))
    assert counts.kept == 2
    assert counts.dropped == {"dropped": 2, "dropped_uncovered_script": 1}


def test_every_cleaned_line_failing_raises_and_writes_nothing():
    class Broken(FakeDetector):
        def spans(self, text):
            raise RuntimeError(f"model down on {SENTINEL}")

    store = Store({raw_name(0): raw_object(numbered(3))})
    with pytest.raises(AllLinesFailed):
        go(store, detector=Broken())
    assert store.writes == {}


def test_main_logs_counts_only(monkeypatch, capsys):
    store = Store({raw_name(0): raw_object([{**numbered(1)[0], "prompt": f"Write to {SENTINEL}"}])})
    monkeypatch.setenv("RAW_BUCKET", BUCKET)
    monkeypatch.setenv("MAX_PROMPTS", "10")
    monkeypatch.setattr(batch, "gcs_lister", lambda: store.list)
    monkeypatch.setattr(batch, "gcs_reader", lambda: store.read)
    monkeypatch.setattr(batch, "r2_writer", lambda: store.write)
    monkeypatch.setattr(batch, "r2_exists", lambda: store.exists)
    monkeypatch.setattr(batch, "gliner_detector", NoDetector)
    assert batch.main() == 0
    err = capsys.readouterr().err
    record = json.loads(err.strip().splitlines()[-1])
    assert record["message"] == "batch done" and record["kept"] == 1 and record["max_prompts"] == 10
    assert "Zéphyrine" not in err and UUIDS[0] not in err and '"prompt"' not in err


def test_main_fails_on_storage_error_with_the_class_name_only(monkeypatch, capsys):
    def boom(bucket, prefix):
        raise PermissionError(f"denied for {SENTINEL}")

    monkeypatch.setenv("RAW_BUCKET", BUCKET)
    monkeypatch.setattr(batch, "gcs_lister", lambda: boom)
    monkeypatch.setattr(batch, "gcs_reader", lambda: None)
    monkeypatch.setattr(batch, "r2_writer", lambda: None)
    monkeypatch.setattr(batch, "r2_exists", lambda: None)
    assert batch.main() == 1
    err = capsys.readouterr().err
    assert "PermissionError" in err and "Zéphyrine" not in err


@pytest.mark.parametrize(("name", "value"), [("RAW_BUCKET", ""), ("MAX_PROMPTS", "0"), ("MAX_SECONDS", "-1")])
def test_main_refuses_bad_settings(monkeypatch, name, value):
    monkeypatch.setenv("RAW_BUCKET", BUCKET)
    monkeypatch.setenv(name, value)
    assert batch.main() == 1
