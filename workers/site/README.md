# site

The Worker behind `https://s1d1.qoo.gy` (everything except `/ingest`, which is `workers/ingest`). It serves the
static landing, relays a closed list of API calls to the origin (tantive, behind a Cloudflare Tunnel), caches the
decisions by the exact body, counts landing views per country, and answers `/status.json`.

No build step and no npm dependency: ES modules, tested on Node 22.

## What it does

| request | answer |
|---|---|
| `GET /` | one visit point (country only), then the static page from `ASSETS` |
| `GET /status.json` | `{"commit", "tantive_image", "model"}` from the `STATUS_*` variables, `Cache-Control: no-store` |
| `/api/*` with `MAINTENANCE` = `"1"` | 503 `{"error": "maintenance"}`, `Retry-After: 3600` (the landing stays up) |
| `POST /api/v1/chat/completions`, `/api/v1/decisions`, `/api/v1/systemone`, `/api/v1/systemone/batch`, `/api/classify`, `/api/predict` | relayed to `ORIGIN` + the same path, through the body-hash cache |
| `GET /api/v1/models`, `/api/models` | relayed, not cached |
| a listed path with the wrong method | 405 with `Allow` |
| any other `/api/*` (`/api/metrics`, `/api/info`, `/api/health`, `/api/v1/systemone/x`, trailing slash, ...) | 404, the origin is not called |
| a `POST` body over 1 MiB (1 048 576 bytes) | 413, the origin is not called |
| anything else (`/llms.txt`, legal pages, ...) | `ASSETS` |

The list is exact: no prefix match, no normalisation beyond what `URL` does. It lives in `src/api.js`.

The `/api` prefix is **kept** when forwarding: tantive runs with `S1D1_ROOT_PATH=/api`, and Starlette strips it before
routing, so the tunnel rewrites nothing. The query string is dropped (no listed endpoint takes one).

Only `Content-Type`, `Accept` and `Authorization` are forwarded. `cf-connecting-ip`, `x-forwarded-for`, `cookie` and
the user agent never reach the origin. An unreachable origin is a 502; any other origin error (429, 503, ...) is
passed through untouched, status, body and `Retry-After` included.

## Bindings and variables

| name | kind | role |
|---|---|---|
| `ASSETS` | binding | static assets, the `site/` folder. `run_worker_first` must be set for `/` and `/status.json` (and `/api/*`), so the Worker sees them before the assets do |
| `VISITS` | binding | Analytics Engine dataset `s1d1_visits`. Optional: if it is missing, or throws, the page is still served |
| `DEPLOY_VERSION` | variable | part of the cache key; changing it empties the cache (a new model, a new label set) |
| `ORIGIN` | variable | `https://s1d1.qoo.gy`: the origin the tunnel reaches (Worker subrequests to the zone go to the origin, not back to the Worker) |
| `CACHE_TTL` | variable | seconds, `86400` by default |
| `MAINTENANCE` | variable | `"1"` to answer 503 on `/api/*` |
| `STATUS_COMMIT`, `STATUS_TANTIVE_IMAGE`, `STATUS_MODEL` | variables | what `/status.json` reports |

Routes: `s1d1.qoo.gy/*` for this Worker, with `s1d1.qoo.gy/ingest` for the ingest Worker (the more specific route
wins). No secret is needed here.

## Why the cache key is the exact body

The key is `https://s1d1.qoo.gy/__cache/<DEPLOY_VERSION>/<path>/<sha256 hex of the body bytes>`. The decision is a
function of the bytes the node receives, so equal bytes can share an answer, and different bytes cannot: `{"a":1}`
and `{ "a": 1 }` are two entries. Parsing and re-serialising the JSON to merge them would be a second implementation
of what the origin understands (key order, number forms, chat message shapes) and a bug there would serve one
caller another caller's decision. Missing a hit costs one inference; a wrong hit costs correctness.

- The body is read once, as bytes (capped at 1 MiB while reading, so a lying `Content-Length` does not help), and the
  same bytes are hashed and forwarded.
- Only a 200 answer to an allowed `POST` is stored. 429, 503, other errors and `GET`s are never put.
- The stored object is a fresh `Response` holding the origin's body with `Content-Type` and
  `Cache-Control: max-age=<CACHE_TTL>` only. The key holds a hash, not the prompt; the request headers (including
  `Authorization`) are not stored.
- The Cache API only keeps `GET` keys, so the key is a bare `GET` request built from that URL.
- The key does not include `Authorization`: if the origin ever gates decisions behind a token, a cached answer would be
  served to a caller without one. The public instance has no such gate.
- `x-s1d1-cache: HIT` or `MISS` tells which path answered. A cache that throws counts as a miss.
- Maintenance is checked before the cache: during maintenance nothing is served from it.

## Privacy

A visit is `writeDataPoint({ blobs: [country], indexes: [country] })`, with `country` from `request.cf?.country` (`XX`
when unknown). No IP and no user agent are recorded, and only `GET /` counts. The Worker never logs a body, a prompt or
a header.

## Tests

From the repository root, on Node 22:

```
node --test "workers/**/*.test.js"
```

`handle(request, env, ctx, { fetch, cache })` takes the origin fetch and the cache as parameters (defaulting to
`globalThis.fetch` and `caches.default`), and `ASSETS` and `VISITS` are fakes in `env`, so the tests use no network.
