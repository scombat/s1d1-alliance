// Fixed-window counters, kept in memory by one Limiter instance (one client of one scope). No I/O, no clock of its
// own: the Durable Object passes Date.now(), the tests pass any time.

const MAX_RULES = 4;
const MAX_PERIOD = 86_400;

/** The rules as given, or a TypeError: 1 to 4 of {name, limit, period}, distinct names, whole numbers. */
function checked(rules) {
  if (!Array.isArray(rules) || rules.length < 1 || rules.length > MAX_RULES) throw new TypeError("rules: 1 to 4");
  const names = new Set();
  for (const r of rules) {
    if (typeof r?.name !== "string" || r.name === "" || names.has(r.name)) throw new TypeError("rules: name");
    if (!Number.isInteger(r.limit) || r.limit < 1) throw new TypeError("rules: limit");
    if (!Number.isInteger(r.period) || r.period < 1 || r.period > MAX_PERIOD) throw new TypeError("rules: period");
    names.add(r.name);
  }
  return rules;
}

/**
 * One more request? Every rule must allow it. A refused request is not counted, so a client that keeps hammering
 * gets in again as soon as its full window ends. `windows` is the instance's Map (rule name -> {start, count}).
 * Returns {ok: true} or {ok: false, retryAfter}, the whole seconds until the latest full window ends (at least 1).
 */
export function hit(windows, rules, now) {
  const current = checked(rules).map((r) => {
    const w = windows.get(r.name);
    return w && now < w.start + r.period * 1000 ? w : { start: now, count: 0 };
  });
  let retryAfter = 0;
  rules.forEach((r, i) => {
    windows.set(r.name, current[i]);
    if (current[i].count >= r.limit) {
      retryAfter = Math.max(retryAfter, Math.ceil((current[i].start + r.period * 1000 - now) / 1000), 1);
    }
  });
  if (retryAfter > 0) return { ok: false, retryAfter };
  for (const w of current) w.count++;
  return { ok: true };
}
