import { test } from "node:test";
import assert from "node:assert/strict";

import { hit } from "../src/windows.js";

const API = [
  { name: "10s", limit: 30, period: 10 },
  { name: "1h", limit: 1000, period: 3600 },
];

test("30 per 10 s: the 31st request in the window is refused, with the seconds left", () => {
  const w = new Map();
  for (let i = 0; i < 30; i++) assert.deepEqual(hit(w, API, 1_000 + i), { ok: true });
  assert.deepEqual(hit(w, API, 4_500), { ok: false, retryAfter: 7 });
});

test("the window ends: the client gets in again", () => {
  const w = new Map();
  for (let i = 0; i < 30; i++) hit(w, API, 0);
  assert.equal(hit(w, API, 9_999).ok, false);
  assert.equal(hit(w, API, 10_000).ok, true);
});

test("a refused request is not counted, in any window", () => {
  const w = new Map();
  for (let i = 0; i < 30; i++) hit(w, API, 0);
  for (let i = 0; i < 50; i++) hit(w, API, 5_000);
  assert.equal(w.get("1h").count, 30);
});

test("1 000 per hour: refused for the rest of the hour even when the 10 s window is fresh", () => {
  const w = new Map();
  let t = 0;
  for (let i = 0; i < 1000; i++) {
    assert.equal(hit(w, API, t).ok, true, `request ${i}`);
    if (i % 30 === 29) t += 10_000;
  }
  const res = hit(w, API, t);
  assert.equal(res.ok, false);
  assert.equal(res.retryAfter, 3600 - t / 1000);
});

test("retryAfter is at least 1 second", () => {
  const w = new Map();
  const rules = [{ name: "a", limit: 1, period: 10 }];
  hit(w, rules, 0);
  assert.deepEqual(hit(w, rules, 9_999), { ok: false, retryAfter: 1 });
});

test("malformed rules are refused", () => {
  for (const rules of [
    undefined,
    [],
    [{ name: "a", limit: 1, period: 10 }, { name: "a", limit: 2, period: 60 }],
    [{ name: "a", limit: 0, period: 10 }],
    [{ name: "a", limit: 1.5, period: 10 }],
    [{ name: "a", limit: 1, period: 0 }],
    [{ name: "a", limit: 1, period: 86_401 }],
    [{ name: "", limit: 1, period: 10 }],
    Array.from({ length: 5 }, (_, i) => ({ name: `r${i}`, limit: 1, period: 10 })),
  ]) {
    assert.throws(() => hit(new Map(), rules, 0), TypeError);
  }
});
