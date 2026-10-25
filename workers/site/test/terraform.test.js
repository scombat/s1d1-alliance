import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

// The /api rate limit is the Limiter Durable Object (LIMITER and LIMIT_SALT). A missing binding makes the Worker fail
// open, so production must declare both; and no zone ruleset may come back, because the zone's single free rate limiting rule
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

test("terraform declares the LIMITER and LIMIT_SALT bindings of the site Worker, and no ratelimit binding", () => {
  const body = siteResource();
  assert.match(
    body,
    /\{ name = "LIMITER", type = "durable_object_namespace", class_name = "Limiter", script_name = cloudflare_workers_script\.limiter\.script_name \}/,
  );
  assert.match(body, /\{ name = "LIMIT_SALT", type = "secret_text", text = random_password\.limit_salt\.result \}/);
  assert.match(body, /"src\/limit\.js" = \{\s*content_file = "\$\{path\.module\}\/\.\.\/workers\/site\/src\/limit\.js"/);
  assert.ok(indexJs.includes('from "./limit.js"'), "src/index.js does not use limit.js");
});

test("no Workers Rate Limiting binding is left (they enforced nothing, docs/31 of the project)", () => {
  assert.doesNotMatch(workersTf, /type\s*=\s*"ratelimit"/);
});

test("the limiter Worker creates the SQLite-backed Limiter class and has no workers.dev address", () => {
  const start = workersTf.indexOf('resource "cloudflare_workers_script" "limiter"');
  assert.notEqual(start, -1, "limiter Worker resource not found");
  const rest = workersTf.slice(start + 1);
  const body = rest.slice(0, rest.search(/^resource /m));
  assert.match(body, /migrations = \{\s*new_tag\s*= "v1"\s*new_sqlite_classes = \["Limiter"\]\s*\}/);
  assert.match(body, /"src\/windows\.js" = \{/);
  assert.match(workersTf, /resource "cloudflare_workers_script_subdomain" "limiter" \{[^}]*enabled\s*= false\s*previews_enabled = false/);
  const limiterJs = readFileSync(join(here, "../../limiter/src/index.js"), "utf8");
  assert.match(limiterJs, /export class Limiter extends DurableObject/);
  assert.doesNotMatch(limiterJs, /\.storage\b/, "the limiter keeps its counters in memory only (no ctx.storage)");
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
