import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

// A missing rate limit binding makes the Worker fail open, so production must declare both. Reading the Terraform
// source is crude but it runs without Terraform and fails the build when a binding is removed or renamed.
const here = dirname(fileURLToPath(import.meta.url));
const workersTf = readFileSync(join(here, "../../../terraform/workers.tf"), "utf8");
const indexJs = readFileSync(join(here, "../src/index.js"), "utf8");

/** The text of `resource "cloudflare_workers_script" "ingest" { ... }`, up to the next top-level resource. */
function ingestResource() {
  const start = workersTf.indexOf('resource "cloudflare_workers_script" "ingest"');
  assert.notEqual(start, -1, "ingest Worker resource not found");
  const rest = workersTf.slice(start + 1);
  const next = rest.search(/^resource /m);
  return next === -1 ? rest : rest.slice(0, next);
}

const EXPECTED = {
  INGEST_BURST: { limit: 100, period: 10 },
  INGEST_SUSTAINED: { limit: 100, period: 60 },
};

for (const [name, { limit, period }] of Object.entries(EXPECTED)) {
  test(`terraform declares the ${name} rate limit binding of the ingest Worker (${limit} per ${period} s)`, () => {
    const lines = ingestResource().split("\n").filter((l) => l.includes(`name = "${name}"`));
    assert.equal(lines.length, 1, `${name} must be declared exactly once in the ingest Worker bindings`);
    assert.match(lines[0], /type\s*=\s*"ratelimit"/);
    assert.match(lines[0], /namespace_id\s*=\s*"\d+"/);
    assert.match(lines[0], new RegExp(`simple\\s*=\\s*\\{\\s*limit\\s*=\\s*${limit},\\s*period\\s*=\\s*${period}\\s*\\}`));
    assert.ok(indexJs.includes(`"${name}"`), `${name} is not used by src/index.js`);
  });
}

test("the two rate limit namespace ids are distinct", () => {
  const ids = [...ingestResource().matchAll(/type = "ratelimit", namespace_id = "(\d+)"/g)].map((m) => m[1]);
  assert.equal(ids.length, 2);
  assert.notEqual(ids[0], ids[1]);
});
