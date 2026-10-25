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
| a listed path, client IP over 100 requests per 10 s | 429 `{"error": "rate_limited"}`, `Retry-After: 10`; the body is not read, the cache and the origin are not touched |
| a `POST` body over 1 MiB (1 048 576 bytes) | 413, the origin is not called |
| anything else (`/llms.txt`, legal pages, ...) | `ASSETS` |

The list is exact: no prefix match, no normalisation beyond what `URL` does. It lives in `src/api.js`.

The `/api` prefix is **kept** when forwarding, and the tunnel rewrites nothing: tantive receives `/api/v1/...`.
`S1D1_ROOT_PATH=/api` alone does not make that path work; tantive 0.8.0 and later handle a request path that starts
with the prefix (fixed in s1d1-tantive PR #5), and smoke test 9.1 of `docs/operations.md` checks
`POST /api/v1/systemone` end to end. The query string is dropped (no listed endpoint takes one).

Only `Content-Type`, `Accept` and `Authorization` are forwarded. `cf-connecting-ip`, `x-forwarded-for`, `cookie` and
the user agent never reach the origin. The origin is fetched with `redirect: "manual"`: a 3xx is passed through to the
caller as it is, never followed by the Worker (which would carry the body and `Authorization` to the `Location`), and
never cached. An unreachable origin, or an origin body that fails while it is read, is a 502; any other origin answer
(429, 503, ...) is passed through untouched, status, body and `Retry-After` included.

## Bindings and variables

| name | kind | role |
|---|---|---|
| `ASSETS` | binding | static assets, the `site/` folder. `run_worker_first` must be set for `/` and `/status.json` (and `/api/*`), so the Worker sees them before the assets do |
| `VISITS` | binding | Analytics Engine dataset `s1d1_visits`. Optional: if it is missing, or throws, the page is still served |
| `DEPLOY_VERSION` | variable | part of the cache key; changing it empties the cache (a new model, a new label set) |
| `ORIGIN` | variable | `https://s1d1.qoo.gy`: the origin the tunnel reaches (Worker subrequests to the zone go to the origin, not back to the Worker) |
| `CACHE_TTL` | variable | seconds, `86400` by default (Terraform refuses 0 or less) |
| `DEBUG_CACHE_HEADER` | variable | `"1"` adds `x-s1d1-cache: HIT` or `MISS`; anything else (Terraform's default `"0"`) leaves it out |
| `ORIGIN_REQUIRES_AUTH` | variable | `"1"` turns the cache off entirely (no match, no put); Terraform's default is `"0"` |
| `API_BURST` | rate limit binding | Workers Rate Limiting, 100 per 10 s per client IP, namespace `1003`. Optional: if it is missing, or throws, the request goes on (fail open) |
| `MAINTENANCE` | variable | `"1"` to answer 503 on `/api/*` |
| `STATUS_COMMIT`, `STATUS_TANTIVE_IMAGE`, `STATUS_MODEL` | variables | what `/status.json` reports |

Routes: `s1d1.qoo.gy/*` for this Worker, with `s1d1.qoo.gy/ingest` for the ingest Worker (the more specific route
wins). No secret is needed here.

## Rate limit

`/api/*` is limited per client IP (`cf-connecting-ip`, `unknown` when absent) by the `API_BURST`
[Workers Rate Limiting](https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/) binding:
100 requests per 10 s (as configured; see "Measured" below, it was not enforced when tested). The landing, `/status.json` and the static assets are never limited.

- The check comes after maintenance and the allow-list (an unknown path is still a 404, a wrong method a 405, and
  neither uses budget) and before the body is read and the cache is consulted. A cache HIT counts like any other
  request: the limit protects the edge as well as the origin.
- Over the limit: 429 `{"error": "rate_limited"}` with `Retry-After: 10`; the origin is not called and the cache is
  not consulted.
- A missing or throwing binding fails open, with one log line that names the binding and holds no request data.
- The count is approximate and per Cloudflare location (the binding is eventually consistent), like the ingest limits.
- **Measured 2026-10-25: not enforced.** 430 requests to `/api` from one IP (110 in under 1 s, 320 over 8 s) were all
  accepted. The Workers logs show the `API_BURST` binding was called and never reported missing or failing, so it
  answered "allowed" every time. Cloudflare documents these limits as approximate and per location. Decision: the
  binding stays in the code (no effect for now) and no other edge limit is added during the first period; the deadline
  shedding of tantive protects its capacity. If abuse shows in the analytics, a Durable Object limiter will be built.
- **There is no zone rule.** The zone `qoo.gy` allows a single rate limiting rule on the Free plan and it is already
  used by another subdomain; Terraform must never manage that zone ruleset (`terraform/cloudflare.tf`).
- **Known trade-off.** When the Workers daily request quota is exhausted, the route fails open (section "Manual steps"
  of `terraform/README.md`): `/api/*` then goes straight to the tunnel, with no allow-list in the Worker and no rate
  limit. The tunnel ingress still lists the API paths only, and the deadline shedding of tantive still protects
  its capacity.
- Terraform: `{ name = "API_BURST", type = "ratelimit", namespace_id = "1003", simple = { limit = 100, period = 10 } }`
  on the Worker's `bindings`; `test/terraform.test.js` checks it stays declared and that no `cloudflare_ruleset`
  exists.

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
  served to a caller without one. The public instance has no such gate; an origin that has one must run with
  `ORIGIN_REQUIRES_AUTH = "1"`, which bypasses the cache entirely (it is neither read nor filled).
- With `DEBUG_CACHE_HEADER = "1"`, `x-s1d1-cache: HIT` or `MISS` tells which path answered; by default the header is
  absent. A cache that throws counts as a miss.
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
