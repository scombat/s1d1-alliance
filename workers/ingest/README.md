# ingest

The Worker behind `POST https://s1d1.qoo.gy/ingest`. It receives NDJSON batches of classified prompts from s1d1
nodes (tantive) and from direct senders, validates them, and writes each batch as one object to the raw Google Cloud
Storage bucket. The contract is `contract/ingest-v1.md`; the examples the tests read are in `contract/fixtures/`.

## What it does

1. Answers 503 `{"error": "maintenance"}` with `Retry-After: 3600` when `MAINTENANCE` is `"1"`.
2. Refuses what is not a `POST` to `/ingest` (405, 404).
3. Applies the per-IP limits (see "Rate limits" below), unless the request carries the community token.
4. Refuses what is not `application/x-ndjson` (415), what is over 1 MiB (413, checked on `Content-Length` and again
   on the bytes actually read) or over 500 lines (413).
5. Parses and validates line by line (`src/validate.js`). A bad line is dropped and counted; the batch is never
   refused for it. Unknown keys are removed; `null` counts as an absent optional key (tantive sends `null`).
6. Adds `source` (`capture:community`, `capture:selfhosted:<uuid>` or `capture:direct`) and `received` (UTC day).
   The community token is compared in constant time.
7. Writes the kept lines as one object `raw/YYYY/MM/DD/<uuid>.ndjson` (`src/gcs.js`). A failed write is a 503, which
   tantive retries. Nothing is written when no line is valid: 202 `{"accepted": 0, "dropped": n}`.

It never logs a body, a line, a header or an IP; the only log lines are a fixed message with the HTTP status of a failed
write and, when a rate limit binding is missing or fails, a fixed message naming the binding.

## Rate limits

Two [Workers Rate Limiting](https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/) bindings
count requests per client IP:

| binding | limit | period | `Retry-After` when refused |
|---|---|---|---|
| `INGEST_BURST` | 100 | 10 s | 10 |
| `INGEST_SUSTAINED` | 100 | 60 s | 60 |

That is at most 10 requests per second and at most 6 000 per hour for one key. Both must allow the request (the
burst is asked first; a request it refuses does not use the sustained budget). Over a limit the answer is 429
`{"error": "rate_limited"}` and nothing is written. The body is not read.

- **Key = the client IP**, `cf-connecting-ip`, or `"unknown"` when the header is absent. Cloudflare advises against
  IP keys because several users can share one address. Here it is still the right key: an `instance_id` is
  chosen by the sender and can be forged, so the IP is the only key a sender cannot invent. A shared address (a
  company NAT) shares the budget; 10 req/s is a generous allowance for a node that sends batches.
- **The community node is exempt.** A request with the community token (same constant-time check as `source`) never
  reaches the limits. Maintenance, method and path checks come before; content type, size and parsing after.
- **tantive treats 429 as a temporary failure**: its cursor stays where it was and it backs off, so a refused batch
  is sent again later and nothing is lost.
- **The limits are approximate and per Cloudflare location**, as Cloudflare documents: the counters are local to the
  data centre that serves the request and eventually consistent, so a client spread over several locations can
  exceed the figures. They stop a loop, not a determined distributed attacker. `/api` keeps the single zone WAF
  rule (`terraform/cloudflare.tf`); this replaces it for `/ingest` only.
- **A missing binding fails open** for that limit (local runs, tests without it), with one fixed log line that
  names the binding and nothing of the request. A binding that throws is treated the same. So that this cannot ship
  unnoticed, `test/terraform.test.js` reads `terraform/workers.tf` and fails if either binding is not declared on
  the ingest Worker with the right limit and period.
- **Terraform**: `type = "ratelimit"`, `namespace_id` and `simple = { limit, period }` on the Worker's `bindings`
  (provider 5.28.1). The namespace ids are integers we choose, unique in the Cloudflare account: **1001** for
  `INGEST_BURST` and **1002** for `INGEST_SUSTAINED`. Cloudflare accepts a period of 10 or 60 seconds only.
- **To verify at the first apply**: the Cloudflare page does not say whether the binding is available on the
  Workers Free plan. If the upload is refused, this is the first thing to look at (smoke test 8 of
  `docs/operations.md`).

## Environment

| name | kind | role |
|---|---|---|
| `RAW_BUCKET` | variable | name of the GCS raw bucket |
| `GCS_HMAC_ID` | variable | HMAC access id of the `ingest-writer` service account |
| `GCS_HMAC_SECRET` | secret | its HMAC secret |
| `COMMUNITY_TOKEN` | secret | the bearer token of the community node |
| `MAINTENANCE` | variable | `"1"` to answer 503 on `/ingest` |
| `INGEST_BURST` | rate limit | 100 per 10 s per IP, namespace 1001 |
| `INGEST_SUSTAINED` | rate limit | 100 per 60 s per IP, namespace 1002 |

No secret is in this repository; they are given to the Worker at deploy time.

## Tests

From the repository root, on Node 22 (no install step, no npm dependency):

```
node --test "workers/**/*.test.js"
```

(A bare directory argument, `node --test workers/`, is not understood by Node 22: it needs the glob.)

The tests inject the writer (`handle(request, env, { putRaw })`), so no network is used, except one test that replaces
`globalThis.fetch` to check the signed request that `putRaw` builds.

## The vendored dependency: aws4fetch 1.0.20

`vendor/aws4fetch.js` is [aws4fetch](https://github.com/mhart/aws4fetch) 1.0.20 (MIT, Michael Hart), the only
third-party code in the Workers. It is there because AWS Signature Version 4 is subtle (canonical request, signed
headers, date scoping, key derivation) and a hand-written signer is a bug waiting to happen on the one path that
carries the data. It signs the `PUT` to GCS's S3-compatible XML API with an HMAC key; the Worker does nothing else with it.

- Source: the published npm package `aws4fetch@1.0.20`, file `dist/aws4fetch.esm.mjs`, copied unchanged (the
  MIT `@license` header is its first lines).
- sha256 of the file: `3049cd99d00a2fb83ce0e77f0e6b3fb22c17d8e708b740821f906145a8f978e9`.
- Licence text: `vendor/LICENSE-aws4fetch`.
- To check it: `npm pack aws4fetch@1.0.20`, unpack, and compare `dist/aws4fetch.esm.mjs` with `vendor/aws4fetch.js`
  (`cmp`, or `shasum -a 256`). Do not edit it; to upgrade, replace the file and this record.

`src/gcs.js` signs with the real SHA-256 of the body (`X-Amz-Content-Sha256`) instead of aws4fetch's `UNSIGNED-PAYLOAD`
default for S3, which not every S3-compatible server accepts, and uses a single retry because tantive retries anyway.
