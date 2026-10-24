# ingest

The Worker behind `POST https://s1d1.qoo.gy/ingest`. It receives NDJSON batches of classified prompts from s1d1
nodes (tantive) and from direct senders, validates them, and writes each batch as one object to the raw Google Cloud
Storage bucket. The contract is `contract/ingest-v1.md`; the examples the tests read are in `contract/fixtures/`.

## What it does

1. Answers 503 `{"error": "maintenance"}` with `Retry-After: 3600` when `MAINTENANCE` is `"1"`.
2. Refuses what is not a `POST` to `/ingest` (405, 404), what is not `application/x-ndjson` (415), what is over
   1 MiB (413, checked on `Content-Length` and again on the bytes actually read) or over 500 lines (413).
3. Parses and validates line by line (`src/validate.js`). A bad line is dropped and counted; the batch is never
   refused for it. Unknown keys are removed; `null` counts as an absent optional key (tantive sends `null`).
4. Adds `source` (`capture:community`, `capture:selfhosted:<uuid>` or `capture:direct`) and `received` (UTC day).
   The community token is compared in constant time.
5. Writes the kept lines as one object `raw/YYYY/MM/DD/<uuid>.ndjson` (`src/gcs.js`). A failed write is a 503, which
   tantive retries. Nothing is written when no line is valid: 202 `{"accepted": 0, "dropped": n}`.

It never logs a body, a line or a header; the only log line is a fixed message with the HTTP status of a failed write.

## Environment

| name | kind | role |
|---|---|---|
| `RAW_BUCKET` | variable | name of the GCS raw bucket |
| `GCS_HMAC_ID` | variable | HMAC access id of the `ingest-writer` service account |
| `GCS_HMAC_SECRET` | secret | its HMAC secret |
| `COMMUNITY_TOKEN` | secret | the bearer token of the community node |
| `MAINTENANCE` | variable | `"1"` to answer 503 on `/ingest` |

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
