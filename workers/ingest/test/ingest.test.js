import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import worker, { handle } from "../src/index.js";
import { validateLine, sourceOf, constantTimeEqual } from "../src/validate.js";
import { putRaw } from "../src/gcs.js";
import { clientKey } from "../src/limit.js";

const here = dirname(fileURLToPath(import.meta.url));
const fixture = (name) => readFileSync(join(here, "../../../contract/fixtures", name), "utf8");
const VALID = fixture("valid.ndjson");
const MIXED = fixture("mixed.ndjson");

const TOKEN = "community-token-for-tests";
const UUID_1 = "6f1c2a9e-3b7d-4e8a-9c21-5d0b8e7f4a12";
const UUID_2 = "a3b1c2d4-5e6f-4a7b-8c9d-0e1f2a3b4c5d";
const SALT = "salt-for-tests";
/** A LIMITER binding (Durable Object namespace) that records the names and rules it is asked about. */
const limiter = ({ ok = true, retryAfter = 9, names = [], rules = [] } = {}) => ({
  idFromName: (name) => (names.push(name), { name }),
  get: () => ({ hit: async (r) => (rules.push(r), ok ? { ok: true } : { ok: false, retryAfter }) }),
});
const baseEnv = { RAW_BUCKET: "raw-test", GCS_HMAC_ID: "id", GCS_HMAC_SECRET: "secret", COMMUNITY_TOKEN: TOKEN };
const env = { ...baseEnv, LIMITER: limiter(), LIMIT_SALT: SALT };

function recorder(fail = false) {
  const calls = [];
  return {
    calls,
    putRaw: async (e, key, body) => {
      calls.push({ env: e, key, body });
      if (fail) throw new Error("storage down");
    },
  };
}

function post(body, headers = {}, method = "POST") {
  return new Request("https://s1d1.qoo.gy/ingest", {
    method,
    headers: { "content-type": "application/x-ndjson", ...headers },
    body: method === "GET" ? undefined : body,
  });
}

const storedLines = (deps) => deps.calls[0].body.trimEnd().split("\n").map((l) => JSON.parse(l));
const today = () => new Date().toISOString().slice(0, 10);

test("a valid batch is accepted and written once", async () => {
  const deps = recorder();
  const res = await handle(post(VALID), env, deps);
  assert.equal(res.status, 202);
  assert.deepEqual(await res.json(), { accepted: 3, dropped: 0 });
  assert.equal(deps.calls.length, 1);
  assert.match(deps.calls[0].key, /^raw\/\d{4}\/\d{2}\/\d{2}\/[0-9a-f-]{36}\.ndjson$/);
  assert.ok(deps.calls[0].body.endsWith("\n"));
});

test("the object key carries the UTC day of reception and is unique per batch", async () => {
  const deps = recorder();
  await handle(post(VALID), env, deps);
  await handle(post(VALID), env, deps);
  assert.ok(deps.calls[0].key.startsWith(`raw/${today().replaceAll("-", "/")}/`));
  assert.notEqual(deps.calls[0].key, deps.calls[1].key);
});

test("one bad line is dropped, not the batch", async () => {
  const deps = recorder();
  const res = await handle(post(MIXED), env, deps);
  assert.equal(res.status, 202);
  assert.deepEqual(await res.json(), { accepted: 3, dropped: 2 });
  assert.equal(storedLines(deps).length, 3);
});

test("every stored line has source and received, and unknown keys are removed", async () => {
  const deps = recorder();
  const extra = JSON.stringify({ ...JSON.parse(VALID.split("\n")[0]), secret_extra: "x", ip: "1.2.3.4", __proto__: { a: 1 } });
  await handle(post(extra + "\n"), env, deps);
  const [line] = storedLines(deps);
  assert.equal(line.received, today());
  assert.match(line.source, /^capture:/);
  assert.equal("secret_extra" in line, false);
  assert.equal("ip" in line, false);
  assert.deepEqual(
    Object.keys(line),
    ["v", "id", "time", "prompt", "format", "instance_id", "variant", "model_revision", "terms_version",
      "pred_tier", "pred_score", "pred_task", "pred_needs_tools", "pred_confidence", "source", "received"],
  );
});

test("the community token yields capture:community", async () => {
  const deps = recorder();
  await handle(post(VALID, { authorization: `Bearer ${TOKEN}` }), env, deps);
  assert.equal(storedLines(deps)[0].source, "capture:community");
});

test("a wrong token never yields community", async () => {
  const deps = recorder();
  await handle(post(VALID, { authorization: "Bearer x" }), env, deps);
  assert.equal(storedLines(deps)[0].source, `capture:selfhosted:${UUID_1}`);
});

test("a token longer or shorter than the real one never matches, nor does a prefix", async () => {
  assert.notEqual(sourceOf({ instance_id: UUID_1 }, `Bearer ${TOKEN}x`, TOKEN), "capture:community");
  assert.notEqual(sourceOf({ instance_id: UUID_1 }, `Bearer ${TOKEN.slice(0, -1)}`, TOKEN), "capture:community");
  assert.notEqual(sourceOf({ instance_id: UUID_1 }, `Bearer `, TOKEN), "capture:community");
  assert.notEqual(sourceOf({ instance_id: UUID_1 }, TOKEN, TOKEN), "capture:community");
});

test("an unset community token never matches an empty bearer", async () => {
  assert.equal(sourceOf({}, "Bearer ", undefined), "capture:direct");
  assert.equal(sourceOf({}, "Bearer ", ""), "capture:direct");
  assert.equal(sourceOf({}, null, TOKEN), "capture:direct");
});

test("a self-hosted uuid yields capture:selfhosted:<uuid>", async () => {
  const deps = recorder();
  await handle(post(VALID), env, deps);
  const lines = storedLines(deps);
  assert.equal(lines[1].source, `capture:selfhosted:${UUID_2}`);
});

test("no instance id yields capture:direct", async () => {
  const deps = recorder();
  await handle(post(VALID), env, deps);
  assert.equal(storedLines(deps)[2].source, "capture:direct");
});

test("a non-uuid instance id yields capture:direct", async () => {
  assert.equal(sourceOf({ instance_id: "not-a-uuid" }, null, TOKEN), "capture:direct");
  assert.equal(sourceOf({ instance_id: `${UUID_1}\nfoo` }, null, TOKEN), "capture:direct");
  assert.equal(sourceOf({ instance_id: 42 }, null, TOKEN), "capture:direct");
});

test("413 above 1 MiB, 415 on another content type, 405 on GET", async () => {
  const deps = recorder();
  const big = "x".repeat(1_048_577);
  assert.equal((await handle(post(big), env, deps)).status, 413);
  // content-length declared too large is refused before reading
  const declared = post("{}\n", { "content-length": "1048577" });
  assert.equal((await handle(declared, env, deps)).status, 413);
  assert.equal((await handle(post(VALID, { "content-type": "application/json" }), env, deps)).status, 415);
  assert.equal((await handle(post(VALID, { "content-type": "text/plain" }), env, deps)).status, 415);
  const get = await handle(post(undefined, {}, "GET"), env, deps);
  assert.equal(get.status, 405);
  assert.equal(get.headers.get("allow"), "POST");
  assert.equal(deps.calls.length, 0);
});

test("exactly 1 MiB is accepted", async () => {
  const deps = recorder();
  const line = (n) => JSON.stringify({ v: 1, id: `i${n}`, prompt: "p".repeat(40_000), terms_version: "v1" }) + "\n";
  let body = "";
  let n = 0;
  while (body.length + line(n).length <= 1_048_576) body += line(n++);
  body += "\n".repeat(1_048_576 - body.length); // pad with blank lines to hit the limit exactly
  assert.equal(new TextEncoder().encode(body).length, 1_048_576);
  const res = await handle(post(body), env, deps);
  assert.equal(res.status, 202);
});

test("a content type with a charset parameter is accepted", async () => {
  const deps = recorder();
  const res = await handle(post(VALID, { "content-type": "application/x-ndjson; charset=utf-8" }), env, deps);
  assert.equal(res.status, 202);
});

test("more than 500 lines is 413, exactly 500 is fine", async () => {
  const deps = recorder();
  const one = JSON.stringify({ v: 1, id: "a", prompt: "p", terms_version: "v1" });
  assert.equal((await handle(post(Array(501).fill(one).join("\n")), env, deps)).status, 413);
  assert.equal(deps.calls.length, 0);
  const ok = await handle(post(Array(500).fill(one).join("\n")), env, deps);
  assert.equal(ok.status, 202);
  assert.deepEqual(await ok.json(), { accepted: 500, dropped: 0 });
});

test("a storage failure is 503 so tantive retries", async () => {
  const deps = recorder(true);
  const res = await handle(post(VALID), env, deps);
  assert.equal(res.status, 503);
});

test("maintenance answers 503 with Retry-After 3600", async () => {
  const deps = recorder();
  const res = await handle(post(VALID), { ...env, MAINTENANCE: "1" }, deps);
  assert.equal(res.status, 503);
  assert.equal(res.headers.get("retry-after"), "3600");
  assert.deepEqual(await res.json(), { error: "maintenance" });
  assert.equal(deps.calls.length, 0);
  const other = await handle(post(VALID), { ...env, MAINTENANCE: "0" }, deps);
  assert.equal(other.status, 202);
});

test("an empty batch is 202 with nothing written", async () => {
  const deps = recorder();
  for (const body of ["", "\n\n  \n"]) {
    const res = await handle(post(body), env, deps);
    assert.equal(res.status, 202);
    assert.deepEqual(await res.json(), { accepted: 0, dropped: 0 });
  }
  assert.equal(deps.calls.length, 0);
});

test("a batch with no valid line is 202 accepted 0 and nothing is written", async () => {
  const deps = recorder();
  const res = await handle(post("nope\n{}\n"), env, deps);
  assert.deepEqual(await res.json(), { accepted: 0, dropped: 2 });
  assert.equal(deps.calls.length, 0);
});

test("the prompt is never in a log line", async () => {
  const logged = [];
  const saved = {};
  for (const m of ["log", "info", "warn", "error", "debug"]) {
    saved[m] = console[m];
    console[m] = (...a) => logged.push(a.map((x) => (x instanceof Error ? x.stack : String(x))).join(" "));
  }
  try {
    const secretPrompt = "ultra-secret-prompt-text";
    const body = JSON.stringify({ v: 1, id: "a", prompt: secretPrompt, terms_version: "v1" }) + "\nnot json " + secretPrompt + "\n";
    await handle(post(body), env, recorder());
    await handle(post(body), env, recorder(true));
    await handle(post(body, { "content-type": "text/plain" }), env, recorder());
    await handle(post(body), { ...env, MAINTENANCE: "1" }, recorder());
    await worker.fetch(post(body), { ...env, RAW_BUCKET: "b" }, {});
  } finally {
    for (const m of Object.keys(saved)) console[m] = saved[m];
  }
  assert.ok(!logged.join("\n").includes("ultra-secret-prompt-text"));
  assert.ok(!logged.join("\n").includes(TOKEN));
});

test("the default export writes through putRaw (GCS) with signed S3-style requests", async () => {
  const seen = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    const req = new Request(input, init);
    seen.push({ url: req.url, method: req.method, headers: req.headers, body: await req.text() });
    return new Response(null, { status: 200 });
  };
  try {
    const res = await worker.fetch(post(VALID), env, {});
    assert.equal(res.status, 202);
  } finally {
    globalThis.fetch = realFetch;
  }
  assert.equal(seen.length, 1);
  assert.equal(seen[0].method, "PUT");
  assert.match(seen[0].url, /^https:\/\/storage\.googleapis\.com\/raw-test\/raw\/\d{4}\/\d{2}\/\d{2}\/[0-9a-f-]{36}\.ndjson$/);
  assert.equal(seen[0].headers.get("content-type"), "application/x-ndjson");
  assert.match(seen[0].headers.get("authorization"), /^AWS4-HMAC-SHA256 Credential=id\/\d{8}\/auto\/s3\/aws4_request, SignedHeaders=.*, Signature=[0-9a-f]{64}$/);
  assert.match(seen[0].headers.get("x-amz-content-sha256"), /^[0-9a-f]{64}$/);
  assert.equal(seen[0].body.split("\n").length, 4);
});

test("putRaw throws on a non-2xx answer and never leaks the body", async () => {
  const realFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response("<Error>AccessDenied</Error>", { status: 403 });
  try {
    await assert.rejects(putRaw(env, "raw/2026/10/24/x.ndjson", "secret body\n"), (e) => {
      assert.match(e.message, /403/);
      assert.ok(!e.message.includes("secret body"));
      return true;
    });
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("validateLine: required keys and ranges", () => {
  const ok = { v: 1, id: "a", prompt: "p", terms_version: "v1" };
  assert.deepEqual(validateLine(ok), ok);
  for (const bad of [
    null, [], "x", 3,
    { ...ok, v: 2 }, { ...ok, v: "1" }, { ...ok, v: undefined },
    { ...ok, id: "" }, { ...ok, id: "x".repeat(65) }, { ...ok, id: 7 }, { ...ok, id: undefined },
    { ...ok, prompt: "" }, { ...ok, prompt: "x".repeat(65_537) }, { ...ok, prompt: 3 }, { ...ok, prompt: undefined },
    { ...ok, terms_version: "" }, { ...ok, terms_version: 1 }, { ...ok, terms_version: undefined },
  ]) {
    assert.equal(validateLine(bad), null, JSON.stringify(bad));
  }
  assert.ok(validateLine({ ...ok, id: "x".repeat(64), prompt: "x".repeat(65_536) }));
});

test("validateLine: the prompt limit counts characters, not UTF-16 units", () => {
  const ok = { v: 1, id: "a", terms_version: "v1" };
  assert.ok(validateLine({ ...ok, prompt: "\u{1F600}".repeat(65_536) }));
  assert.equal(validateLine({ ...ok, prompt: "\u{1F600}".repeat(65_537) }), null);
});

test("validateLine: optional keys are typed, null means absent, bad type drops the line", () => {
  const ok = { v: 1, id: "a", prompt: "p", terms_version: "v1" };
  // tantive sends null for a missing optional key
  assert.deepEqual(validateLine({ ...ok, time: null, pred_score: null, format: null }), ok);
  assert.equal(validateLine({ ...ok, pred_score: "0.5" }), null);
  assert.equal(validateLine({ ...ok, pred_score: Infinity }), null);
  assert.equal(validateLine({ ...ok, pred_confidence: NaN }), null);
  assert.equal(validateLine({ ...ok, pred_tier: 3 }), null);
  assert.equal(validateLine({ ...ok, format: "x".repeat(257) }), null);
  assert.equal(validateLine({ ...ok, instance_id: {} }), null);
  assert.deepEqual(validateLine({ ...ok, pred_score: 0, pred_needs_tools: 0.2 }), { ...ok, pred_score: 0, pred_needs_tools: 0.2 });
});

test("constantTimeEqual", () => {
  assert.equal(constantTimeEqual("abc", "abc"), true);
  assert.equal(constantTimeEqual("abc", "abd"), false);
  assert.equal(constantTimeEqual("abc", "abcd"), false);
  assert.equal(constantTimeEqual("", ""), true);
  assert.equal(constantTimeEqual("é", "é"), true);
});

// ---- per-IP rate limits -------------------------------------------------------------------------------------

/** Run `fn` while collecting everything written to the console. */
async function withLogs(fn) {
  const logged = [];
  const saved = {};
  for (const m of ["log", "info", "warn", "error", "debug"]) {
    saved[m] = console[m];
    console[m] = (...a) => logged.push(a.map((x) => (x instanceof Error ? x.stack : String(x))).join(" "));
  }
  try {
    await fn();
  } finally {
    for (const m of Object.keys(saved)) console[m] = saved[m];
  }
  return logged.join("\n");
}

test("over a limit: 429 rate_limited, Retry-After from the limiter, nothing written", async () => {
  const deps = recorder();
  const e = { ...env, LIMITER: limiter({ ok: false }) };
  const res = await handle(post(VALID, { "cf-connecting-ip": "203.0.113.7" }), e, deps);
  assert.equal(res.status, 429);
  assert.equal(res.headers.get("retry-after"), "9");
  assert.equal(res.headers.get("content-type"), "application/json");
  assert.deepEqual(await res.json(), { error: "rate_limited" });
  assert.equal(deps.calls.length, 0);
});

test("within the limits the batch goes through, and the limiter is asked once with the /ingest rules", async () => {
  const rules = [];
  const e = { ...env, LIMITER: limiter({ rules }) };
  const deps = recorder();
  const res = await handle(post(VALID, { "cf-connecting-ip": "203.0.113.7" }), e, deps);
  assert.equal(res.status, 202);
  assert.equal(deps.calls.length, 1);
  assert.deepEqual(rules, [[{ name: "10s", limit: 100, period: 10 }, { name: "60s", limit: 100, period: 60 }]]);
});

test("the limiter instance is ingest:<HMAC of cf-connecting-ip, or of unknown>; an instance id is not a key", async () => {
  const names = [];
  const e = { ...env, LIMITER: limiter({ names }) };
  await handle(post(VALID, { "cf-connecting-ip": "2001:db8::1", "x-forwarded-for": "198.51.100.9" }), e, recorder());
  await handle(post(VALID), e, recorder());
  assert.deepEqual(names, [`ingest:${await clientKey(SALT, "2001:db8::1")}`, `ingest:${await clientKey(SALT, "unknown")}`]);
  assert.ok(!names.join().includes("2001:db8"));
});

test("the community token is exempt even when the limiter refuses, and it is not even asked", async () => {
  const names = [];
  const e = { ...env, LIMITER: limiter({ ok: false, names }) };
  const deps = recorder();
  const res = await handle(post(VALID, { authorization: `Bearer ${TOKEN}`, "cf-connecting-ip": "203.0.113.7" }), e, deps);
  assert.equal(res.status, 202);
  assert.equal(deps.calls.length, 1);
  assert.deepEqual(names, []);
  // A wrong token is not exempt.
  const wrong = await handle(post(VALID, { authorization: "Bearer nope" }), e, recorder());
  assert.equal(wrong.status, 429);
});

test("the checks before the limits stay before: maintenance, method and path are not rate limited", async () => {
  const names = [];
  const e = { ...env, LIMITER: limiter({ ok: false, names }) };
  assert.equal((await handle(post(VALID), { ...e, MAINTENANCE: "1" }, recorder())).status, 503);
  assert.equal((await handle(post(undefined, {}, "GET"), e, recorder())).status, 405);
  const other = new Request("https://s1d1.qoo.gy/other", { method: "POST", body: "x" });
  assert.equal((await handle(other, e, recorder())).status, 404);
  assert.deepEqual(names, []);
  // After the limits: a request over the limit is refused with 429 before its content type is judged.
  assert.equal((await handle(post(VALID, { "content-type": "text/plain" }), e, recorder())).status, 429);
});

test("a missing LIMITER or LIMIT_SALT, or a throwing limiter, fails open with one log line and no request data", async () => {
  const noSalt = { ...env };
  delete noSalt.LIMIT_SALT;
  const throwing = {
    ...env,
    LIMITER: { idFromName: () => ({}), get: () => ({ hit: async () => { throw new Error("down 203.0.113.7"); } }) },
  };
  const statuses = [];
  const logs = await withLogs(async () => {
    for (const e of [baseEnv, noSalt, throwing]) {
      const deps = recorder();
      statuses.push((await handle(post(VALID, { "cf-connecting-ip": "203.0.113.7" }), e, deps)).status);
      assert.equal(deps.calls.length, 1);
    }
  });
  assert.deepEqual(statuses, [202, 202, 202]);
  const lines = logs.split("\n");
  assert.deepEqual(lines, [
    "ingest: limiter is missing, not enforced",
    "ingest: limiter is missing, not enforced",
    "ingest: limiter failed, not enforced",
  ]);
});

test("a 429 never logs the body, the token or the ip", async () => {
  const secret = "ultra-secret-prompt-text";
  const body = JSON.stringify({ v: 1, id: "a", prompt: secret, terms_version: "v1" }) + "\n";
  const e = { ...env, LIMITER: limiter({ ok: false }) };
  let res;
  const logs = await withLogs(async () => {
    res = await handle(post(body, { authorization: "Bearer not-the-token", "cf-connecting-ip": "203.0.113.7" }), e, recorder());
    await handle(post(body, { authorization: `Bearer ${TOKEN}` }), e, recorder());
    await worker.fetch(post(body, { "cf-connecting-ip": "203.0.113.7" }), e, {});
  });
  assert.equal(res.status, 429);
  assert.ok(!(await res.text()).includes(secret));
  assert.ok(!logs.includes(secret));
  assert.ok(!logs.includes(TOKEN));
  assert.ok(!logs.includes("not-the-token"));
  assert.ok(!logs.includes("203.0.113.7"));
});
