// The closed list of API paths the public site relays to the origin (tantive, served under /api).
// Anything else under /api is a 404: /api/metrics, /api/info and /api/health are never reachable from outside.

export const API_PREFIX = "/api";

export const ALLOWED_POST = [
  "/api/v1/chat/completions",
  "/api/v1/decisions",
  "/api/v1/systemone",
  "/api/v1/systemone/batch",
  "/api/classify",
  "/api/predict",
];

export const ALLOWED_GET = ["/api/v1/models", "/api/models"];

/** True when `method` on `path` (the full path, /api prefix included) is on the list. Exact match, no normalisation. */
export function isAllowed(method, path) {
  if (method === "POST") return ALLOWED_POST.includes(path);
  if (method === "GET") return ALLOWED_GET.includes(path);
  return false;
}

/** The methods a path accepts, for the Allow header of a 405; empty when the path is not on the list at all. */
export function methodsFor(path) {
  const methods = [];
  if (ALLOWED_GET.includes(path)) methods.push("GET");
  if (ALLOWED_POST.includes(path)) methods.push("POST");
  return methods;
}

export function isApiPath(path) {
  return path === API_PREFIX || path.startsWith(`${API_PREFIX}/`);
}
