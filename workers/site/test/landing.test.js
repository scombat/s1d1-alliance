import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (p) => readFileSync(new URL(`../../../site/${p}`, import.meta.url), "utf8");
const html = read("index.html");
const js = read("app.js");
const txt = read("llms.txt");

test("the /api URL, the tutorial and the try box are hidden until the box is checked", () => {
  assert.match(html, /<div id="tutorial" hidden>/);
  const start = html.indexOf('<div id="tutorial" hidden>');
  const end = html.indexOf('<h2 id="self-host">');
  assert.ok(start >= 0 && end > start, "the gated block ends where the self-host section starts");
  const gated = html.slice(start, end);
  for (const id of ["api-url", "try-box"]) assert.match(gated, new RegExp(`id="${id}"`), id);
  for (const m of html.matchAll(/s1d1\.qoo\.gy\/api/g)) {
    assert.ok(m.index > start && m.index < end, `the API URL at ${m.index} is outside the gated block`);
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

test("each legal page is written, in French, with no placeholder left", () => {
  for (const p of ["conditions/v1", "confidentialite/v1", "mentions-legales"]) {
    const h = read(`${p}/index.html`);
    assert.match(h, /<h1>[^<]+<\/h1>/);
    assert.match(h, /lang="fr"/);
    assert.doesNotMatch(h, /À rédiger/);
    assert.doesNotMatch(h, /\{\{[A-Z_]+\}\}/, `${p}: a {{FIELD}} is still to fill`);
  }
});

test("the terms say that using the API is acceptance and that what is sent is kept for retraining", () => {
  const h = read("conditions/v1/index.html");
  assert.match(h, /Utiliser l'API du service[^.]*vaut également acceptation/);
  assert.match(h, /irrévocable/);
  assert.match(h, /N'envoyez pas de données personnelles/);
});

test("the privacy policy states the raw retention and the subprocessors", () => {
  const h = read("confidentialite/v1/index.html");
  assert.match(h, /au plus 4 jours/);
  for (const name of ["Cloudflare", "Google Cloud", "Hetzner"]) assert.match(h, new RegExp(name));
  assert.doesNotMatch(h, /Oracle/);
});

test("LiteLLM is documented as System One with api_base at the service root /api", () => {
  for (const [name, t] of [["html", html], ["llms", txt]]) {
    assert.match(t, /oss_classifier/, name);
    assert.match(t, /before 1\.106/i, name);
  }
  assert.match(html, /api_base: https:\/\/s1d1\.qoo\.gy\/api\n/); // the YAML of the LiteLLM tab
  assert.match(txt, /"api_base": "https:\/\/s1d1\.qoo\.gy\/api"/);
  assert.match(html + txt, /classifier_fallback: default_model|classifier_fallback "default_model"/);
  assert.match(html, /Base URL: <code>https:\/\/s1d1\.qoo\.gy\/api<\/code>/);
});

test("app.js applies the gate state once at load", () => {
  assert.match(js, /\n\s*toggle\(\);/);
  assert.match(js, /gate\.checked/);
});
