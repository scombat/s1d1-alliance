# Ingestion contract, v1

This is the contract of `POST https://s1d1.qoo.gy/ingest`, the endpoint that receives batches of classified prompts
from s1d1 nodes and from direct senders. The sending side is `to_ingest` and `send.py` in s1d1-tantive
(`src/tantive/send.py`, `docs/capture.md`); this document is the receiving side. The fixtures in `contract/fixtures/`
are the examples the tests read.

## The batch

- `POST /ingest` with `Content-Type: application/x-ndjson`: one JSON object per line, UTF-8, `\n` separators.
- At most **1 048 576 bytes** (1 MiB) and **500 lines**.
- Optional `Authorization: Bearer <token>`. A token that matches the community token gives the `community` source
  (see below); any other value, or none, is not an error.

## The line (v1)

Keys sent by the client:

| key | type and rule | required |
|---|---|---|
| `v` | the number `1` | yes |
| `id` | string, 1 to 64 characters | yes |
| `time` | string, the capture time (ISO 8601, UTC) | no |
| `prompt` | non-empty string, at most 65 536 characters | yes |
| `format` | string (the API format that classified the prompt, for instance `systemone`) | no |
| `instance_id` | string, a UUID when it is used as a source (see below) | no |
| `variant` | string (the weights that decided, for instance `small`) | no |
| `model_revision` | string (the revision of the weights) | no |
| `terms_version` | non-empty string (the version of the terms in force, for instance `v1`) | yes |
| `pred_tier` | string (the native tier, for instance `MEDIUM`) | no |
| `pred_score` | number | no |
| `pred_task` | string (for instance `coding_debugging`) | no |
| `pred_needs_tools` | number, a probability | no |
| `pred_confidence` | number | no |

The optional keys are typed as `to_ingest` types them in tantive. A key that is not in this table is removed; the line
is kept. A line whose `v` is not `1`, whose `id` or `prompt` is missing or out of range, or whose `terms_version` is
missing or empty is invalid (see the batch rules).

The Worker adds two keys to every kept line before it is stored:

- `source`: one of the three values below;
- `received`: the UTC day the batch was received, `YYYY-MM-DD`.

The fixtures: `valid.ndjson` has three valid lines, one per source (the tests send it with `Authorization: Bearer <community token>`, so line 1 is the community case; line 2 carries a self-hosted UUID and line 3 has no `instance_id`, so it is direct); `mixed.ndjson` has the same three plus one line
without `terms_version` and one line that is not JSON.

## The sources

Checked in this order, the first that holds gives the source:

1. `capture:community` when the `Authorization: Bearer` value equals the community token. The comparison is done in
   constant time. A wrong token never gives `community`.
2. `capture:selfhosted:<instance_id>` when `instance_id` is a UUID.
3. `capture:direct` otherwise.

## Responses

| status | body | when |
|---|---|---|
| 202 | `{"accepted": n, "dropped": m}` | the batch was read. Invalid lines are dropped and counted in `dropped`; the other lines are kept, whatever their number. A batch with no valid line is 202 with `accepted: 0`. |
| 405 | | the method is not `POST` |
| 413 | | the body is over 1 MiB or has more than 500 lines |
| 415 | | `Content-Type` is not `application/x-ndjson` |
| 503 | | the write to the raw bucket failed. tantive retries a 503 later; the same batch may come again, so the storage key is unique per batch. Nothing in this repository deduplicates (neither the Worker nor the anonymizer): a batch sent twice gives two raw objects and two clean objects holding the same `id`s, and consumers of the clean corpus must deduplicate by `id` (the s1d1 research pipeline removes duplicates at import). |
| 503 | `{"error": "maintenance"}` with `Retry-After: 3600` | maintenance is on (`MAINTENANCE = "1"`) |

tantive treats 202 as sent. It treats 400 and 413 as rejected and never sends that batch again. It retries any other
answer (503, timeouts, network errors) after a back-off. A 202 with `dropped > 0` is not a rejection: the good lines
are stored.

## Storage

Each accepted batch is written once, as one object, to the raw bucket:

`raw/AAAA/MM/JJ/<uuid>.ndjson`

The object holds the kept lines with `source` and `received`, in the order received. The raw bucket keeps objects for
3 days. The anonymiser writes the clean copy to `clean/AAAA/MM/JJ/<same uuid>.ndjson`; a rewrite replaces the object,
so one event received twice produces one clean record (a batch sent twice is two events, see the 503 row above).

## What is never stored

The clean copy keeps the decision-11 keys only: `id`, `time` (day only), `prompt` (anonymised), `source`,
`model_revision`, `terms_version`, `format`, `pred_tier`, `pred_score`, `pred_task`, `pred_needs_tools` and
`pred_confidence`. It never keeps `instance_id`, the IP address or the request headers.
