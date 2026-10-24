// Site Worker for s1d1.qoo.gy: static landing (ASSETS), the /api allow-list relayed to the origin, a body-hash
// cache for the decision endpoints, one visit point per landing view (country only), and /status.json.
// /api/* is rate limited per client IP by the Workers Rate Limiting binding API_BURST (there is no zone rule: the
// zone's single free rule belongs to another subdomain). Nothing here logs a body, a prompt, an IP or a header.
import { isAllowed, isApiPath, methodsFor } from "./api.js";
import { DEFAULT_TTL, cacheKey, keyRequest, storable } from "./cache.js";

export const MAX_BODY = 1_048_576;

// The only request headers relayed to the origin. cf-connecting-ip, x-forwarded-for and cookie are never copied.
const FORWARDED_HEADERS = ["content-type", "accept", "authorization"];

const json = (status, body, headers = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });

const empty = (status, headers = {}) => new Response(null, { status, headers });

/**
 * True when the client IP is over the /api limit. A binding that is missing (local run, tests) or that throws does
 * not stop the request (fail open): the log line names the binding only, never a request datum.
 */
async function overLimit(env, request) {
  const binding = env.API_BURST;
  if (typeof binding?.limit !== "function") {
    console.warn("site: rate limit binding API_BURST is missing, not enforced");
    return false;
  }
  try {
    const { success } = await binding.limit({ key: request.headers.get("cf-connecting-ip") || "unknown" });
    return !success;
  } catch {
    console.warn("site: rate limit binding API_BURST failed, not enforced");
    return false;
  }
}

/** The body as bytes, or null when it is over MAX_BODY (checked on Content-Length, then while reading). */
async function readCapped(request) {
  const declared = request.headers.get("content-length");
  if (declared !== null && Number(declared) > MAX_BODY) return null;
  if (!request.body) return new Uint8Array(0);
  const reader = request.body.getReader();
  const chunks = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > MAX_BODY) {
      await reader.cancel();
      return null;
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return bytes;
}

function later(ctx, promise) {
  const safe = promise.catch(() => {});
  if (ctx?.waitUntil) ctx.waitUntil(safe);
  return safe;
}

async function handleApi(request, env, ctx, deps, path) {
  if (env.MAINTENANCE === "1") return json(503, { error: "maintenance" }, { "retry-after": "3600" });

  const method = request.method;
  if (!isAllowed(method, path)) {
    const accepted = methodsFor(path);
    return accepted.length > 0 ? empty(405, { allow: accepted.join(", ") }) : empty(404);
  }

  // After the allow-list (an unknown path is a 404 that uses no budget), before the body and the cache: a cache HIT
  // counts too, the limit protects the edge as well as the origin.
  if (await overLimit(env, request)) return json(429, { error: "rate_limited" }, { "retry-after": "10" });

  const doFetch = deps.fetch ?? globalThis.fetch;
  const isPost = method === "POST";

  let body;
  if (isPost) {
    body = await readCapped(request);
    if (body === null) return empty(413);
  }

  // The cache key holds no Authorization: when the origin gates decisions behind a token, a cached answer would go to
  // a caller without one, so ORIGIN_REQUIRES_AUTH = "1" turns the cache off (no match, no put).
  const debugHeader = env.DEBUG_CACHE_HEADER === "1";
  let key = null;
  const cache = isPost && env.ORIGIN_REQUIRES_AUTH !== "1" ? (deps.cache ?? globalThis.caches?.default) : undefined;
  if (cache) {
    key = keyRequest(await cacheKey(env.DEPLOY_VERSION ?? "", path, body));
    try {
      const hit = await cache.match(key);
      if (hit) return debugHeader ? withHeader(hit, "x-s1d1-cache", "HIT") : hit;
    } catch {
      // A broken cache is a miss, never an error for the caller.
    }
  }

  const headers = new Headers();
  for (const name of FORWARDED_HEADERS) {
    const value = request.headers.get(name);
    if (value !== null) headers.set(name, value);
  }
  // redirect "manual": a 3xx of the origin is passed through to the caller, never followed by the Worker (which would
  // send the body and the Authorization header wherever the Location points).
  let origin;
  let decision;
  try {
    origin = await doFetch(
      new Request(env.ORIGIN + path, { method, headers, body: isPost ? body : undefined, redirect: "manual" }),
    );
    if (!isPost || origin.status !== 200) return origin; // 3xx, 429, 503, ... pass through untouched
    decision = new Uint8Array(await origin.arrayBuffer());
  } catch {
    return json(502, { error: "origin_unreachable" });
  }

  if (cache) {
    const ttl = env.CACHE_TTL ?? DEFAULT_TTL;
    later(ctx, Promise.resolve().then(() => cache.put(key, storable(decision, origin.headers.get("content-type"), ttl))));
  }
  const response = new Response(decision, { status: 200, headers: origin.headers });
  return debugHeader ? withHeader(response, "x-s1d1-cache", "MISS") : response;
}

function withHeader(response, name, value) {
  const headers = new Headers(response.headers);
  headers.set(name, value);
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
}

function recordVisit(request, env) {
  try {
    const country = request.cf?.country ?? "XX";
    env.VISITS?.writeDataPoint({ blobs: [country], indexes: [country] });
  } catch {
    // Counting visits must never break the page.
  }
}

/** The request handler; `deps.fetch` (the origin) and `deps.cache` are replaced in the tests. */
export async function handle(request, env, ctx, deps = {}) {
  const url = new URL(request.url);
  const path = url.pathname;

  if (isApiPath(path)) return handleApi(request, env, ctx, deps, path);

  if (path === "/status.json") {
    if (request.method !== "GET" && request.method !== "HEAD") return empty(405, { allow: "GET, HEAD" });
    return json(
      200,
      { commit: env.STATUS_COMMIT, tantive_image: env.STATUS_TANTIVE_IMAGE, model: env.STATUS_MODEL },
      { "cache-control": "no-store" },
    );
  }

  if (path === "/" && request.method === "GET") recordVisit(request, env);
  return env.ASSETS.fetch(request);
}

export default {
  fetch(request, env, ctx) {
    return handle(request, env, ctx);
  },
};

export { isAllowed, cacheKey };
