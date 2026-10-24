import { test } from "node:test";
import assert from "node:assert/strict";

import worker, { handle } from "../src/index.js";
import { isAllowed, ALLOWED_POST, ALLOWED_GET } from "../src/api.js";
import { cacheKey } from "../src/cache.js";

const ORIGIN = "https://origin.example";
const baseEnv = {
  ORIGIN,
  DEPLOY_VERSION: "d1",
  CACHE_TTL: "86400",
  MAINTENANCE: "0",
  STATUS_COMMIT: "abc1234",
  STATUS_TANTIVE_IMAGE: "ghcr.io/scombat/s1d1-tantive:0.8.0-small-onnx-int8@sha256:deadbeef",
  STATUS_MODEL: "s1d1-small",
};
const DECISION = JSON.stringify({ tier: "mid", score: 0.42 });

/** Fakes: the Cache API (only GET keys), the origin, ASSETS and the Analytics Engine dataset. */
function fakes({ originStatus = 200, originBody = DECISION, originHeaders = {} } = {}) {
  const store = new Map();
  const puts = [];
  const originCalls = [];
  const assetCalls = [];
  const points = [];
  const cache = {
    match: async (req) => {
      const hit = store.get(req.url);
      return hit ? hit.clone() : undefined;
    },
    put: async (req, res) => {
      assert.equal(req.method, "GET", "the Cache API only stores GET keys");
      puts.push({ req, res: res.clone() });
      store.set(req.url, res.clone());
    },
  };
  const fetch = async (req) => {
    originCalls.push({
      url: req.url,
      method: req.method,
      headers: Object.fromEntries(req.headers),
      body: new Uint8Array(await req.clone().arrayBuffer()),
    });
    return new Response(originBody, {
      status: originStatus,
      headers: { "content-type": "application/json", ...originHeaders },
    });
  };
  const env = {
    ...baseEnv,
    ASSETS: { fetch: async (req) => (assetCalls.push(new URL(req.url).pathname), new Response("asset")) },
    VISITS: { writeDataPoint: (p) => points.push(p) },
  };
  const waits = [];
  const ctx = { waitUntil: (p) => waits.push(p) };
  const settle = () => Promise.all(waits);
  return { cache, fetch, env, ctx, settle, store, puts, originCalls, assetCalls, points };
}

const api = (path, body = "{}", { method = "POST", headers = {} } = {}) =>
  new Request(`https://s1d1.qoo.gy${path}`, {
    method,
    headers: { "content-type": "application/json", ...headers },
    body: method === "GET" || method === "HEAD" ? undefined : body,
  });

const run = (f, request, envOverride = {}) =>
  handle(request, { ...f.env, ...envOverride }, f.ctx, { fetch: f.fetch, cache: f.cache });

test("allow-list: the eight paths pass, /api/metrics /api/info /api/health and /api/v1/systemone/x are 404", async () => {
  for (const p of ["/v1/chat/completions", "/v1/decisions", "/v1/systemone", "/v1/systemone/batch", "/classify", "/predict"]) {
    assert.equal(isAllowed("POST", `/api${p}`), true, p);
  }
  for (const p of ["/v1/models", "/models"]) assert.equal(isAllowed("GET", `/api${p}`), true, p);
  assert.equal(ALLOWED_POST.length + ALLOWED_GET.length, 8);

  for (const p of ["/api/metrics", "/api/info", "/api/health", "/api/v1/systemone/x", "/api/", "/api", "/api/v1/decisions/", "/api/%76%31/decisions", "/api/v1/decisions%2f", "/api/V1/decisions"]) {
    for (const method of ["GET", "POST"]) {
      assert.equal(isAllowed(method, p), false, `${method} ${p}`);
    }
    const f = fakes();
    const res = await run(f, api(p));
    assert.equal(res.status, 404, p);
    assert.equal(f.originCalls.length, 0, `${p} must not reach the origin`);
  }
});

test("a dot-dot path cannot reach the origin through /api", async () => {
  const f = fakes();
  await run(f, api("/api/../v1/decisions"));
  await run(f, api("/apiv1/decisions"));
  assert.equal(f.originCalls.length, 0);
});

test("GET on a POST path is 405, POST on /api/v1/models is 405", async () => {
  const f = fakes();
  const get = await run(f, api("/api/v1/decisions", "", { method: "GET" }));
  assert.equal(get.status, 405);
  assert.equal(get.headers.get("allow"), "POST");
  const post = await run(f, api("/api/v1/models", "{}"));
  assert.equal(post.status, 405);
  assert.equal(post.headers.get("allow"), "GET");
  assert.equal(f.originCalls.length, 0);
});

test("the /api prefix is kept when forwarding to the origin", async () => {
  const f = fakes();
  await run(f, api("/api/v1/decisions", '{"a":1}'));
  assert.equal(f.originCalls.length, 1);
  assert.equal(f.originCalls[0].url, `${ORIGIN}/api/v1/decisions`);
  assert.equal(f.originCalls[0].method, "POST");
  assert.equal(new TextDecoder().decode(f.originCalls[0].body), '{"a":1}');
});

test("only Content-Type, Accept and Authorization are forwarded; never the client IP, cookies or the query string", async () => {
  const f = fakes();
  const req = new Request("https://s1d1.qoo.gy/api/classify?debug=1", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      accept: "application/json",
      authorization: "Bearer t",
      "cf-connecting-ip": "203.0.113.9",
      "x-forwarded-for": "203.0.113.9",
      cookie: "a=b",
      "user-agent": "curl/8",
    },
    body: "{}",
  });
  await run(f, req);
  const h = f.originCalls[0].headers;
  assert.equal(h["content-type"], "application/json");
  assert.equal(h.accept, "application/json");
  assert.equal(h.authorization, "Bearer t");
  for (const name of ["cf-connecting-ip", "x-forwarded-for", "cookie", "user-agent"]) {
    assert.equal(name in h, false, name);
  }
  assert.equal(f.originCalls[0].url, `${ORIGIN}/api/classify`);
});

test("GET /api/v1/models is forwarded without a body and is never cached", async () => {
  const f = fakes({ originBody: '{"data":[]}' });
  const res = await run(f, api("/api/v1/models", "", { method: "GET" }));
  assert.equal(res.status, 200);
  assert.equal(f.originCalls[0].method, "GET");
  assert.equal(f.originCalls[0].body.byteLength, 0);
  await f.settle();
  assert.equal(f.puts.length, 0);
});

test("cache key is the exact body", async () => {
  const enc = (s) => new TextEncoder().encode(s);
  const a = await cacheKey("d1", "/api/v1/decisions", enc('{"a":1}'));
  const b = await cacheKey("d1", "/api/v1/decisions", enc('{ "a": 1 }'));
  const a2 = await cacheKey("d1", "/api/v1/decisions", enc('{"a":1}'));
  assert.notEqual(a, b);
  assert.equal(a, a2);
  assert.match(a, /^https:\/\/s1d1\.qoo\.gy\/__cache\/d1\/api\/v1\/decisions\/[0-9a-f]{64}$/);
  // also across paths: the same body on two endpoints is two entries
  assert.notEqual(a, await cacheKey("d1", "/api/classify", enc('{"a":1}')));
});

test("cache key matches a known sha256", async () => {
  const key = await cacheKey("v", "/api/predict", new TextEncoder().encode("abc"));
  assert.ok(key.endsWith("/ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"));
});

test("cache key changes with DEPLOY_VERSION", async () => {
  const body = new TextEncoder().encode("{}");
  assert.notEqual(await cacheKey("d1", "/api/predict", body), await cacheKey("d2", "/api/predict", body));
});

test("a hit is served without calling the origin", async () => {
  const f = fakes();
  const first = await run(f, api("/api/v1/decisions", '{"prompt":"hello"}'));
  assert.equal(first.status, 200);
  await f.settle();
  assert.equal(f.originCalls.length, 1);
  assert.equal(f.puts.length, 1);

  const second = await run(f, api("/api/v1/decisions", '{"prompt":"hello"}'));
  assert.equal(second.status, 200);
  assert.equal(await second.text(), DECISION);
  assert.equal(f.originCalls.length, 1, "no second origin call");
});

test("a different body or a new DEPLOY_VERSION is a miss", async () => {
  const f = fakes();
  await run(f, api("/api/v1/decisions", '{"prompt":"hello"}'));
  await f.settle();
  await run(f, api("/api/v1/decisions", '{"prompt": "hello"}'));
  await run(f, api("/api/v1/decisions", '{"prompt":"hello"}'), { DEPLOY_VERSION: "d2" });
  assert.equal(f.originCalls.length, 3);
});

test("only 200 POST responses are cached", async () => {
  for (const status of [429, 503, 400, 500]) {
    const f = fakes({ originStatus: status, originBody: '{"error":"x"}', originHeaders: { "retry-after": "7" } });
    const res = await run(f, api("/api/v1/decisions", '{"p":1}'));
    await f.settle();
    assert.equal(res.status, status);
    assert.equal(res.headers.get("retry-after"), "7", "origin errors pass through, Retry-After included");
    assert.equal(await res.text(), '{"error":"x"}');
    assert.equal(f.puts.length, 0, `${status} must not be stored`);
  }
  const f = fakes();
  await run(f, api("/api/v1/models", "", { method: "GET" }));
  await f.settle();
  assert.equal(f.puts.length, 0);
});

test("the cached response holds the decision, with Cache-Control max-age=CACHE_TTL, and no request data", async () => {
  const f = fakes({ originHeaders: { "set-cookie": "s=1", "x-secret": "no" } });
  const body = '{"prompt":"my secret prompt"}';
  const res = await run(f, api("/api/v1/decisions", body, { headers: { authorization: "Bearer topsecret", "x-extra": "y" } }), {
    CACHE_TTL: "600",
  });
  assert.equal(await res.text(), DECISION);
  await f.settle();
  assert.equal(f.puts.length, 1);
  const { req, res: stored } = f.puts[0];
  assert.equal(req.method, "GET");
  assert.equal(await stored.text(), DECISION);
  assert.equal(stored.headers.get("cache-control"), "max-age=600");
  assert.equal(stored.headers.get("content-type"), "application/json");
  assert.deepEqual([...stored.headers.keys()].sort(), ["cache-control", "content-type"]);
  // the key holds the hash of the body, not the prompt
  assert.equal(req.url.includes("secret"), false);
  assert.equal(req.url.includes("prompt"), false);
  assert.equal([...req.headers.keys()].includes("authorization"), false);
});

test("CACHE_TTL defaults to 86400 when unset", async () => {
  const f = fakes();
  await run(f, api("/api/classify", "{}"), { CACHE_TTL: undefined });
  await f.settle();
  assert.equal(f.puts[0].res.headers.get("cache-control"), "max-age=86400");
});

test("a failing cache neither breaks the request nor the response", async () => {
  const f = fakes();
  f.cache.match = async () => {
    throw new Error("cache down");
  };
  f.cache.put = async () => {
    throw new Error("cache down");
  };
  const res = await run(f, api("/api/classify", "{}"));
  await f.settle();
  assert.equal(res.status, 200);
  assert.equal(await res.text(), DECISION);
});

test("a request body above 1 MiB is 413 without calling the origin", async () => {
  const f = fakes();
  const big = "x".repeat(1_048_577);
  const res = await run(f, api("/api/v1/decisions", big));
  assert.equal(res.status, 413);
  assert.equal(f.originCalls.length, 0);

  // exactly 1 MiB still goes through
  const ok = await run(f, api("/api/v1/decisions", "x".repeat(1_048_576)));
  assert.equal(ok.status, 200);
  assert.equal(f.originCalls.length, 1);
});

test("a lying Content-Length does not get past the cap; the real length is checked too", async () => {
  const f = fakes();
  const declared = api("/api/v1/decisions", "{}", { headers: { "content-length": "2000000" } });
  assert.equal((await run(f, declared)).status, 413);
  // a body stream longer than 1 MiB without a Content-Length
  const chunk = new Uint8Array(600_000);
  const stream = new ReadableStream({
    start(c) {
      c.enqueue(chunk);
      c.enqueue(chunk);
      c.close();
    },
  });
  const chunked = new Request("https://s1d1.qoo.gy/api/v1/decisions", {
    method: "POST",
    body: stream,
    duplex: "half",
    headers: { "content-type": "application/json" },
  });
  assert.equal((await run(f, chunked)).status, 413);
  assert.equal(f.originCalls.length, 0);
});

test("maintenance: /api is 503 Retry-After 3600, / still served", async () => {
  const f = fakes();
  const res = await run(f, api("/api/v1/decisions", "{}"), { MAINTENANCE: "1" });
  assert.equal(res.status, 503);
  assert.equal(res.headers.get("retry-after"), "3600");
  assert.deepEqual(await res.json(), { error: "maintenance" });
  const get = await run(f, api("/api/v1/models", "", { method: "GET" }), { MAINTENANCE: "1" });
  assert.equal(get.status, 503);
  assert.equal(f.originCalls.length, 0);

  const home = await run(f, new Request("https://s1d1.qoo.gy/"), { MAINTENANCE: "1" });
  assert.equal(home.status, 200);
  assert.equal(await home.text(), "asset");
});

test("a hit is not served during maintenance", async () => {
  const f = fakes();
  await run(f, api("/api/classify", "{}"));
  await f.settle();
  const res = await run(f, api("/api/classify", "{}"), { MAINTENANCE: "1" });
  assert.equal(res.status, 503);
});

test("/ writes one visit point with the country only", async () => {
  const f = fakes();
  const req = new Request("https://s1d1.qoo.gy/", {
    headers: { "user-agent": "Mozilla/5.0", "cf-connecting-ip": "203.0.113.9" },
  });
  Object.defineProperty(req, "cf", { value: { country: "FR", city: "Paris" } });
  const res = await run(f, req);
  assert.equal(res.status, 200);
  assert.equal(await res.text(), "asset");
  assert.deepEqual(f.points, [{ blobs: ["FR"], indexes: ["FR"] }]);
  assert.equal(JSON.stringify(f.points).includes("203.0.113.9"), false);
  assert.equal(JSON.stringify(f.points).includes("Mozilla"), false);
});

test("the country defaults to XX; only GET / counts; a missing VISITS binding does not break the page", async () => {
  const f = fakes();
  await run(f, new Request("https://s1d1.qoo.gy/"));
  assert.deepEqual(f.points, [{ blobs: ["XX"], indexes: ["XX"] }]);

  await run(f, new Request("https://s1d1.qoo.gy/", { method: "HEAD" }));
  await run(f, new Request("https://s1d1.qoo.gy/llms.txt"));
  await run(f, new Request("https://s1d1.qoo.gy/status.json"));
  await run(f, api("/api/classify", "{}"));
  assert.equal(f.points.length, 1);

  const noBinding = { ...f.env };
  delete noBinding.VISITS;
  const res = await handle(new Request("https://s1d1.qoo.gy/"), noBinding, f.ctx, { fetch: f.fetch, cache: f.cache });
  assert.equal(res.status, 200);

  const throwing = { ...f.env, VISITS: { writeDataPoint: () => { throw new Error("AE down"); } } };
  const res2 = await handle(new Request("https://s1d1.qoo.gy/"), throwing, f.ctx, { fetch: f.fetch, cache: f.cache });
  assert.equal(res2.status, 200);
});

test("/status.json returns commit, tantive_image and model, never cached", async () => {
  const f = fakes();
  const res = await run(f, new Request("https://s1d1.qoo.gy/status.json"));
  assert.equal(res.status, 200);
  assert.equal(res.headers.get("content-type"), "application/json");
  assert.equal(res.headers.get("cache-control"), "no-store");
  assert.deepEqual(await res.json(), {
    commit: baseEnv.STATUS_COMMIT,
    tantive_image: baseEnv.STATUS_TANTIVE_IMAGE,
    model: baseEnv.STATUS_MODEL,
  });
  assert.equal(f.assetCalls.length, 0);
  assert.equal(f.points.length, 0);
});

test("a request to any other path goes to ASSETS", async () => {
  const f = fakes();
  for (const p of ["/llms.txt", "/conditions/v1/", "/mentions-legales/", "/style.css", "/ingest"]) {
    const res = await run(f, new Request(`https://s1d1.qoo.gy${p}`));
    assert.equal(await res.text(), "asset", p);
  }
  assert.deepEqual(f.assetCalls, ["/llms.txt", "/conditions/v1/", "/mentions-legales/", "/style.css", "/ingest"]);
  assert.equal(f.originCalls.length, 0);
});

test("an unreachable origin is a 502, not an exception", async () => {
  const f = fakes();
  f.fetch = async () => {
    throw new TypeError("network");
  };
  const res = await run(f, api("/api/classify", "{}"));
  assert.equal(res.status, 502);
});

test("the default export delegates to handle with the global fetch and caches.default", async () => {
  const f = fakes();
  const calls = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (req) => (calls.push(req.url), new Response(DECISION, { headers: { "content-type": "application/json" } }));
  globalThis.caches = { default: f.cache };
  try {
    const res = await worker.fetch(api("/api/classify", "{}"), f.env, f.ctx);
    assert.equal(res.status, 200);
    assert.deepEqual(calls, [`${ORIGIN}/api/classify`]);
    await f.settle();
    assert.equal(f.puts.length, 1);
  } finally {
    globalThis.fetch = realFetch;
    delete globalThis.caches;
  }
});
