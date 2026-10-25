// Ingestion Worker: POST /ingest receives NDJSON batches of classified prompts (contract/ingest-v1.md),
// validates each line, adds `source` and `received`, and writes the batch as one object to the raw bucket.
// Nothing here logs a body, a line or a header.
// Per-IP limits (Workers Rate Limiting bindings INGEST_BURST and INGEST_SUSTAINED) are asked for everyone except the
// community node, which is recognised by its token first. Measured on 2026-10-25: Cloudflare answers "allowed" every
// time at our scale, so they do not block in practice (see README.md).
import { putRaw } from "./gcs.js";
import { isCommunity, sourceOf, validateLine } from "./validate.js";

export const MAX_BYTES = 1_048_576;
export const MAX_LINES = 500;

/** The two bindings in the order they are asked, with the Retry-After each one gives. */
const LIMITS = [
  ["INGEST_BURST", "10"],
  ["INGEST_SUSTAINED", "60"],
];

const json = (status, body, headers = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });

const empty = (status, headers = {}) => new Response(null, { status, headers });

/** The body as text, or null when it is over MAX_BYTES (checked while reading, so a huge body is not buffered). */
async function readBody(request) {
  const declared = request.headers.get("content-length");
  if (declared !== null && Number(declared) > MAX_BYTES) return null;
  if (!request.body) return "";
  const reader = request.body.getReader();
  const chunks = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > MAX_BYTES) {
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
  return new TextDecoder().decode(bytes);
}

/**
 * Retry-After in seconds when the client is over a limit, or null when it may go on. Both bindings must allow the
 * request; the burst limit is asked first, so a request it refuses does not use up the sustained budget. A binding
 * that is missing (local run, tests) or that throws does not stop the request: the log line names the binding only.
 */
async function overLimit(env, ip) {
  for (const [name, retryAfter] of LIMITS) {
    const binding = env[name];
    if (typeof binding?.limit !== "function") {
      console.warn(`ingest: rate limit binding ${name} is missing, not enforced`);
      continue;
    }
    try {
      const { success } = await binding.limit({ key: ip });
      if (!success) return retryAfter;
    } catch {
      console.warn(`ingest: rate limit binding ${name} failed, not enforced`);
    }
  }
  return null;
}

function isNdjson(request) {
  const type = (request.headers.get("content-type") ?? "").split(";")[0].trim().toLowerCase();
  return type === "application/x-ndjson";
}

/** The request handler; `deps.putRaw` and `deps.now` are replaced in the tests. */
export async function handle(request, env, deps = {}) {
  const write = deps.putRaw ?? putRaw;
  const now = deps.now ? deps.now() : new Date();

  if (env.MAINTENANCE === "1") return json(503, { error: "maintenance" }, { "retry-after": "3600" });
  if (request.method !== "POST") return empty(405, { allow: "POST" });
  if (new URL(request.url).pathname !== "/ingest") return empty(404);

  if (!isCommunity(request.headers.get("authorization"), env.COMMUNITY_TOKEN)) {
    // The client IP is the only key an attacker cannot forge (an instance_id can be anything).
    const retryAfter = await overLimit(env, request.headers.get("cf-connecting-ip") || "unknown");
    if (retryAfter !== null) return json(429, { error: "rate_limited" }, { "retry-after": retryAfter });
  }

  if (!isNdjson(request)) return empty(415);

  const text = await readBody(request);
  if (text === null) return empty(413);

  const rawLines = text.split("\n").map((l) => l.trim()).filter((l) => l.length > 0);
  if (rawLines.length > MAX_LINES) return empty(413);

  const authorization = request.headers.get("authorization");
  const day = now.toISOString().slice(0, 10);
  const kept = [];
  let dropped = 0;
  for (const raw of rawLines) {
    let parsed;
    try {
      parsed = JSON.parse(raw);
    } catch {
      dropped++;
      continue;
    }
    const line = validateLine(parsed);
    if (line === null) {
      dropped++;
      continue;
    }
    kept.push(JSON.stringify({ ...line, source: sourceOf(line, authorization, env.COMMUNITY_TOKEN), received: day }));
  }

  if (kept.length > 0) {
    const key = `raw/${day.replaceAll("-", "/")}/${crypto.randomUUID()}.ndjson`;
    try {
      await write(env, key, kept.join("\n") + "\n");
    } catch (error) {
      // A fixed message and the status only: never the error text, which a writer might build from the body.
      console.error("ingest: raw write failed", error?.status ?? "");
      return empty(503);
    }
  }
  return json(202, { accepted: kept.length, dropped });
}

export default {
  fetch(request, env, _ctx) {
    return handle(request, env);
  },
};
