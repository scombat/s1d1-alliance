// Validation of one ingestion line (v1) and the source of a line. No I/O, no logging.

export const MAX_PROMPT_CHARS = 65_536;
export const MAX_ID_CHARS = 64;
const MAX_SHORT_STRING = 256;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// Optional keys, in the order they are stored, with their type. A key not listed here (and not required below)
// is removed. `null` counts as absent: tantive sends null for a key it does not have.
const OPTIONAL = [
  ["time", "string"],
  ["format", "string"],
  ["instance_id", "string"],
  ["variant", "string"],
  ["model_revision", "string"],
  ["pred_tier", "string"],
  ["pred_score", "number"],
  ["pred_task", "string"],
  ["pred_needs_tools", "number"],
  ["pred_confidence", "number"],
];

// Characters, as Python's len() counts them in tantive (code points, not UTF-16 units).
function charLength(text) {
  if (text.length <= MAX_PROMPT_CHARS) return text.length;
  let n = 0;
  for (const _ of text) n++;
  return n;
}

function boundedString(value, max) {
  return typeof value === "string" && value.length > 0 && charLength(value) <= max;
}

/** The cleaned v1 line, or null when the line is invalid. */
export function validateLine(obj) {
  if (obj === null || typeof obj !== "object" || Array.isArray(obj)) return null;
  if (obj.v !== 1) return null;
  if (!boundedString(obj.id, MAX_ID_CHARS)) return null;
  if (!boundedString(obj.prompt, MAX_PROMPT_CHARS)) return null;
  if (typeof obj.terms_version !== "string" || obj.terms_version.length === 0 || obj.terms_version.length > MAX_SHORT_STRING) {
    return null;
  }

  const out = { v: 1, id: obj.id };
  const optional = {};
  for (const [key, type] of OPTIONAL) {
    const value = obj[key];
    if (value === undefined || value === null) continue;
    if (type === "number") {
      if (typeof value !== "number" || !Number.isFinite(value)) return null;
    } else if (typeof value !== "string" || value.length > MAX_SHORT_STRING) {
      return null;
    }
    optional[key] = value;
  }
  // Stored order: v, id, time, prompt, format, instance_id, variant, model_revision, terms_version, pred_*
  if ("time" in optional) out.time = optional.time;
  out.prompt = obj.prompt;
  for (const key of ["format", "instance_id", "variant", "model_revision"]) {
    if (key in optional) out[key] = optional[key];
  }
  out.terms_version = obj.terms_version;
  for (const key of ["pred_tier", "pred_score", "pred_task", "pred_needs_tools", "pred_confidence"]) {
    if (key in optional) out[key] = optional[key];
  }
  return out;
}

/**
 * Compare two strings in time that depends on the longer one only: the loop runs over the longer length and
 * the length difference is folded into the result, so neither a mismatch position nor a length mismatch
 * ends it early.
 */
export function constantTimeEqual(a, b) {
  const enc = new TextEncoder();
  const x = enc.encode(a);
  const y = enc.encode(b);
  const n = Math.max(x.length, y.length);
  let diff = x.length ^ y.length;
  for (let i = 0; i < n; i++) diff |= (x[i] ?? 0) ^ (y[i] ?? 0);
  return diff === 0;
}

function bearer(authorization) {
  if (typeof authorization !== "string") return null;
  const m = /^Bearer (.*)$/i.exec(authorization);
  return m ? m[1] : null;
}

/** capture:community, capture:selfhosted:<instance_id> or capture:direct, in that order. */
export function sourceOf(line, authorization, communityToken) {
  const token = bearer(authorization);
  // Always run the comparison when a token is configured, so a missing header costs the same as a wrong one.
  if (typeof communityToken === "string" && communityToken.length > 0) {
    const match = constantTimeEqual(token ?? "", communityToken);
    if (match && token !== null && token.length > 0) return "capture:community";
  }
  const id = line?.instance_id;
  if (typeof id === "string" && UUID.test(id)) return `capture:selfhosted:${id.toLowerCase()}`;
  return "capture:direct";
}
