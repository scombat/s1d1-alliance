# limiter

The `s1d1-limiter` Worker: it holds the `Limiter` Durable Object that the `site` and `ingest` Workers ask before
serving a client (docs/31 of the project). It has no route and no workers.dev address; only their `LIMITER` bindings
reach it.

## Why a Durable Object

The Workers Rate Limiting binding counts per Cloudflare location, from a local cache synchronised in the background.
Cloudflare documents it as approximate and points to a Durable Object for a strict limit. Measured on 2026-10-25,
it blocked nothing: 430 requests to `/api` and 130 to `/ingest` from one IP were all accepted. A Durable Object is one
instance per name, run in one place at a time, so every request of a client goes through the same counter.

## What it does

- One instance per client and scope, named by the caller `api:<hmac>` or `ingest:<hmac>`, where `<hmac>` is the hex
  HMAC-SHA-256 of the client IP under `LIMIT_SALT` (`src/limit.js` of the two Workers). The IP never reaches this
  Worker.
- One RPC method, `hit(rules)`: `rules` is 1 to 4 fixed windows `{name, limit, period}` (period in seconds). Every
  window must allow the request; a refused request is not counted. The answer is `{ok: true}` or
  `{ok: false, retryAfter}` (seconds until the latest full window ends). `src/windows.js` holds the logic, with no
  I/O, so `test/windows.test.js` runs on plain Node.
- **Nothing is written to storage.** The counters live in the instance's memory. An instance left idle is evicted
  with its counters, which only happens to a client that has stopped sending; a client that keeps sending keeps its
  instance, and its count. The class is SQLite-backed only because it is the only kind on the Workers Free plan.

| caller | windows |
|---|---|
| `site`, `/api/*` | 30 per 10 s, 1 000 per hour |
| `ingest`, `/ingest` (community token exempt) | 100 per 10 s, 100 per 60 s |

## Cost

One Durable Object request per limited call. The Free plan gives 100 000 a day, the same as the Workers' own daily
request quota, so the limiter never runs out before the Workers do. Over the quota nothing is billed.

## Deployment

`terraform/workers.tf`, resource `cloudflare_workers_script.limiter`, with the migration `v1` that creates the class.
The provider sends that migration with every upload of this script (see the comment there): keep it small and change
it rarely.

## Tests

```bash
node --test "workers/**/*.test.js"
```

`test/windows.test.js` covers the windows; `test/client.test.js` checks that the two copies of `limit.js` are
identical and that the client name is the HMAC, never the IP.
