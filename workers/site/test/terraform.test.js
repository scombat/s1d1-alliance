import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

// The /api rate limit lives in the site Worker (API_BURST). A missing binding makes the Worker fail open, so
// production must declare it; and no zone ruleset may come back, because the zone's single free rate limiting rule
// belongs to another subdomain. Reading the Terraform source is crude but runs without Terraform.
const here = dirname(fileURLToPath(import.meta.url));
const tfDir = join(here, "../../../terraform");
const workersTf = readFileSync(join(tfDir, "workers.tf"), "utf8");
const indexJs = readFileSync(join(here, "../src/index.js"), "utf8");

/** The text of `resource "cloudflare_workers_script" "site" { ... }`, up to the next top-level resource. */
function siteResource() {
  const start = workersTf.indexOf('resource "cloudflare_workers_script" "site"');
  assert.notEqual(start, -1, "site Worker resource not found");
  const rest = workersTf.slice(start + 1);
  const next = rest.search(/^resource /m);
  return next === -1 ? rest : rest.slice(0, next);
}

test("terraform declares the API_BURST rate limit binding of the site Worker (100 per 10 s)", () => {
  const lines = siteResource().split("\n").filter((l) => l.includes('name = "API_BURST"'));
  assert.equal(lines.length, 1, "API_BURST must be declared exactly once in the site Worker bindings");
  assert.match(lines[0], /type\s*=\s*"ratelimit"/);
  assert.match(lines[0], /namespace_id\s*=\s*"1003"/);
  assert.match(lines[0], /simple\s*=\s*\{\s*limit\s*=\s*100,\s*period\s*=\s*10\s*\}/);
  assert.ok(indexJs.includes("API_BURST"), "API_BURST is not used by src/index.js");
});

test("the rate limit namespace ids are distinct across the two Workers", () => {
  const ids = [...workersTf.matchAll(/type = "ratelimit", namespace_id = "(\d+)"/g)].map((m) => m[1]);
  assert.equal(ids.length, 3);
  assert.equal(new Set(ids).size, 3);
});

test("no cloudflare_ruleset resource exists in terraform/*.tf (the zone's free rule is another subdomain's)", () => {
  const files = readdirSync(tfDir).filter((f) => f.endsWith(".tf"));
  assert.ok(files.length > 0);
  for (const file of files) {
    const code = readFileSync(join(tfDir, file), "utf8")
      .split("\n")
      .filter((l) => !l.trimStart().startsWith("#"))
      .join("\n");
    assert.doesNotMatch(code, /["\s]cloudflare_ruleset["\s]/, `${file} declares a cloudflare_ruleset`);
    assert.doesNotMatch(code, /http_ratelimit/, `${file} mentions the http_ratelimit phase`);
  }
});

// /status.json must show what the nodes are pinned to: STATUS_TANTIVE_IMAGE comes from node/desired.env, not a variable.
const desiredEnvPath = join(here, "../../../node/desired.env");
test("terraform takes STATUS_TANTIVE_IMAGE from the TANTIVE_IMAGE line of node/desired.env", () => {
  assert.match(workersTf, /tantive_image\s*=\s*regex\("\(\?m\)\^TANTIVE_IMAGE=\(\\\\S\+\)\$", file\("\$\{path\.module\}\/\.\.\/node\/desired\.env"\)\)\[0\]/);
  assert.match(workersTf, /name = "STATUS_TANTIVE_IMAGE", type = "plain_text", text = local\.tantive_image /);
  assert.doesNotMatch(readFileSync(join(tfDir, "variables.tf"), "utf8"), /variable "tantive_image"/);
});

test("node/desired.env pins TANTIVE_IMAGE by digest", () => {
  const lines = readFileSync(desiredEnvPath, "utf8").split("\n").filter((l) => l.startsWith("TANTIVE_IMAGE="));
  assert.equal(lines.length, 1, "exactly one TANTIVE_IMAGE line");
  assert.match(lines[0].slice("TANTIVE_IMAGE=".length), /^ghcr\.io\/scombat\/s1d1-tantive:[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}$/);
});
