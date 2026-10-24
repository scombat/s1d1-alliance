import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (p) => readFileSync(new URL(`../../../site/${p}`, import.meta.url), "utf8");
const html = read("index.html");
const js = read("app.js");
const txt = read("llms.txt");

test("the /api URL, the tutorial and the try box are hidden until the box is checked", () => {
  for (const id of ["api-url", "tutorial", "try-box"]) {
    assert.match(html, new RegExp(`<[a-z]+[^>]*id="${id}"[^>]*\\bhidden\\b|<[a-z]+[^>]*\\bhidden\\b[^>]*id="${id}"`), id);
  }
  assert.match(js, /\.hidden\s*=\s*false|removeAttribute\(["']hidden["']\)/);
  assert.match(js, /["']change["']/);
});

test("the checkbox label states that everything sent is kept and used to retrain s1d1 and links /conditions/v1/", () => {
  const label = html.match(/<label[^>]*>[\s\S]*?<input[^>]*type="checkbox"[\s\S]*?<\/label>/);
  assert.ok(label, "a label wrapping the checkbox");
  assert.match(label[0], /everything you send to this service is kept and used to retrain s1d1/i);
  assert.match(label[0], /href="\/conditions\/v1\/"/);
});

test("the page links the Hub model, s1d1-tantive, the demo Space and s1d1-alliance", () => {
  for (const u of [
    "https://huggingface.co/scombat/s1d1-router",
    "https://github.com/scombat/s1d1-tantive",
    "https://huggingface.co/spaces/scombat/s1d1-router-demo",
    "https://github.com/scombat/s1d1-alliance",
    "https://github.com/scombat/s1d1-tantive/blob/main/docs/capture.md",
    "https://github.com/scombat/s1d1-alliance/blob/main/contract/ingest-v1.md",
  ]) assert.ok(html.includes(u), u);
});

test("the try box posts to /api/v1/systemone on the same origin", () => {
  assert.match(js, /fetch\(["']\/api\/v1\/systemone["']/);
  assert.match(js, /questions/);
});

test("no third-party script, no cookie, no analytics tag", () => {
  assert.doesNotMatch(html, /<script[^>]+src="https?:/i);
  assert.doesNotMatch(html, /<link[^>]+href="https?:[^>]*stylesheet|<link[^>]+stylesheet[^>]*href="https?:/i);
  assert.doesNotMatch(js, /document\.cookie|localStorage|sessionStorage/);
  assert.doesNotMatch(html + js, /gtag|analytics|googletagmanager|plausible/i);
});

test("llms.txt starts with the terms, then what s1d1-alliance is, then the API", () => {
  const terms = txt.search(/kept and used to retrain/i);
  const what = txt.search(/s1d1-alliance is/i);
  const api = txt.search(/https:\/\/s1d1\.qoo\.gy\/api\n/);
  assert.ok(terms >= 0 && what > terms && api > what, `${terms} ${what} ${api}`);
  assert.ok(terms < 600, "terms come first");
  assert.match(txt, /tell your human/i);
  assert.match(txt, /100 requests per 10 s/);
  assert.match(txt, /429/);
  assert.ok(txt.includes("https://github.com/scombat/s1d1-tantive/blob/main/llms.txt"));
});

test("llms.txt states no unmeasured savings figure", () => {
  for (const line of txt.split("\n")) {
    assert.doesNotMatch(line, /\d\s*%.*(sav|cost)|(sav|cost).*\d\s*%/i, line);
  }
});

test("each legal page is a template marked 'À rédiger et faire relire par le juriste'", () => {
  for (const p of ["conditions/v1", "confidentialite/v1", "mentions-legales"]) {
    const h = read(`${p}/index.html`);
    assert.match(h, /<h1>[^<]+<\/h1>/);
    assert.match(h, /À rédiger et faire relire par le juriste\./);
    assert.match(h, /lang="fr"/);
  }
});

test("LiteLLM is documented as System One with api_base at the service root /api", () => {
  for (const [name, t] of [["html", html], ["llms", txt]]) {
    assert.match(t, /oss_classifier/, name);
    assert.match(t, /"api_base": "https:\/\/s1d1\.qoo\.gy\/api"/, name);
    assert.match(t, /before 1\.106/i, name);
  }
  assert.match(html, /Base URL: <code>https:\/\/s1d1\.qoo\.gy\/api<\/code>/);
});

test("app.js applies the gate state once at load", () => {
  assert.match(js, /\n\s*toggle\(\);/);
  assert.match(js, /gate\.checked/);
});
