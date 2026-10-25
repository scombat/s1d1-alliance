import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

// A missing LIMITER binding or LIMIT_SALT makes the Worker fail open, so production must declare both. Reading the Terraform
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

test("terraform declares the LIMITER and LIMIT_SALT bindings of the ingest Worker, and its limit.js", () => {
  const body = ingestResource();
  assert.match(
    body,
    /\{ name = "LIMITER", type = "durable_object_namespace", class_name = "Limiter", script_name = cloudflare_workers_script\.limiter\.script_name \}/,
  );
  assert.match(body, /\{ name = "LIMIT_SALT", type = "secret_text", text = random_password\.limit_salt\.result \}/);
  assert.match(body, /"src\/limit\.js" = \{\s*content_file = "\$\{path\.module\}\/\.\.\/workers\/ingest\/src\/limit\.js"/);
  assert.doesNotMatch(body, /"ratelimit"/);
  assert.ok(indexJs.includes('from "./limit.js"'), "src/index.js does not use limit.js");
});

// Invocation logs store per-request metadata (request, response, client details) at Cloudflare, against the privacy
// policy (no IP or headers recorded). They are pinned off in Terraform so a dashboard change is reverted by the next apply.
for (const name of ["site", "ingest", "limiter"]) {
  test(`terraform declares invocation logs disabled on the ${name} Worker`, () => {
    const start = workersTf.indexOf(`resource "cloudflare_workers_script" "${name}"`);
    assert.notEqual(start, -1, `${name} Worker resource not found`);
    const rest = workersTf.slice(start + 1);
    const next = rest.search(/^resource /m);
    const body = next === -1 ? rest : rest.slice(0, next);
    assert.match(body, /^\s*invocation_logs\s*=\s*false\s*$/m, `${name}: invocation_logs must be false`);
    assert.doesNotMatch(body, /invocation_logs\s*=\s*true/, `${name}: invocation_logs must never be true`);
  });
}

// Refresh copies every non-null observability value the settings API returns into the state (provider 5.28.1). A
// value the API fills in by itself but the block leaves unset (logs and traces `destinations`, the traces' sampling
// rate) makes each plan show both Workers as changed in place. terraform/README.md, Workers Logs.
for (const name of ["site", "ingest", "limiter"]) {
  test(`terraform writes out the observability values the API returns on the ${name} Worker`, () => {
    const start = workersTf.indexOf(`resource "cloudflare_workers_script" "${name}"`);
    assert.notEqual(start, -1, `${name} Worker resource not found`);
    const rest = workersTf.slice(start + 1);
    const next = rest.search(/^resource /m);
    const body = next === -1 ? rest : rest.slice(0, next);
    for (const block of ["logs", "traces"]) {
      const m = body.match(new RegExp(`^\\s*${block} = \\{\\n([^}]*)\\}`, "m"));
      assert.ok(m, `${name}: observability.${block} block not found`);
      assert.match(m[1], /^\s*destinations\s*=\s*\[\]\s*$/m, `${name}: ${block}.destinations must be []`);
      assert.match(m[1], /^\s*head_sampling_rate\s*=\s*1\s*$/m, `${name}: ${block}.head_sampling_rate must be 1`);
      assert.match(m[1], /^\s*persist\s*=\s*true\s*$/m, `${name}: ${block}.persist must be true`);
    }
  });
}
