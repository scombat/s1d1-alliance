// Body-hash cache for the decision endpoints. The key is the exact bytes of the request body, hashed:
// {"a":1} and { "a": 1 } are two entries, and the prompt itself never appears in the key.

export const CACHE_HOST = "https://s1d1.qoo.gy";
export const DEFAULT_TTL = 86_400;

async function sha256Hex(bytes) {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** https://s1d1.qoo.gy/__cache/<deployVersion>/<path without its leading slash>/<sha256 hex of the body> */
export async function cacheKey(deployVersion, path, bodyBytes) {
  return `${CACHE_HOST}/__cache/${deployVersion}/${path.replace(/^\/+/, "")}/${await sha256Hex(bodyBytes)}`;
}

/** The Cache API only stores GET requests: the key is a bare GET Request, with no header of the client's. */
export const keyRequest = (key) => new Request(key, { method: "GET" });

/** A fresh Response holding the origin's JSON body, with Content-Type and Cache-Control only. */
export function storable(bodyBytes, contentType, ttl) {
  return new Response(bodyBytes, {
    status: 200,
    headers: { "content-type": contentType ?? "application/json", "cache-control": `max-age=${ttl}` },
  });
}
