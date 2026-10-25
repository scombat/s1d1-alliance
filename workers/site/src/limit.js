// Per-client limits, asked of the Limiter Durable Object of the s1d1-limiter Worker (workers/limiter). The same file
// is in workers/site/src and workers/ingest/src, because each Worker is uploaded from its own folder;
// workers/limiter/test/client.test.js fails if the two copies differ.
//
// The client is named by an HMAC-SHA-256 of its IP under LIMIT_SALT (a secret of the Worker): the IP never leaves
// the Worker, never reaches the limiter and is never logged.

const encoder = new TextEncoder();

/** The hex HMAC-SHA-256 of `ip` under `salt`. */
export async function clientKey(salt, ip) {
  const key = await crypto.subtle.importKey("raw", encoder.encode(salt), { name: "HMAC", hash: "SHA-256" }, false, [
    "sign",
  ]);
  const mac = new Uint8Array(await crypto.subtle.sign("HMAC", key, encoder.encode(ip)));
  return Array.from(mac, (b) => b.toString(16).padStart(2, "0")).join("");
}

/**
 * Retry-After in seconds (a string) when the client is over one of `rules`, or null when it may go on. A missing
 * LIMITER binding or LIMIT_SALT, or a limiter that throws, does not stop the request (fail open, docs/31 of the
 * project): the log line names the scope only, never a request datum.
 */
export async function overLimit(env, scope, ip, rules) {
  if (typeof env.LIMITER?.idFromName !== "function" || !env.LIMIT_SALT) {
    console.warn(`${scope}: limiter is missing, not enforced`);
    return null;
  }
  try {
    const name = `${scope}:${await clientKey(env.LIMIT_SALT, ip)}`;
    const { ok, retryAfter } = await env.LIMITER.get(env.LIMITER.idFromName(name)).hit(rules);
    return ok ? null : String(retryAfter);
  } catch {
    console.warn(`${scope}: limiter failed, not enforced`);
    return null;
  }
}
