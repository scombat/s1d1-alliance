import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { clientKey } from "../../site/src/limit.js";

const here = dirname(fileURLToPath(import.meta.url));

test("the site and ingest copies of limit.js are identical", () => {
  const site = readFileSync(join(here, "../../site/src/limit.js"), "utf8");
  const ingest = readFileSync(join(here, "../../ingest/src/limit.js"), "utf8");
  assert.equal(ingest, site);
});

test("clientKey is the hex HMAC-SHA-256 (RFC 4231 test case 2)", async () => {
  assert.equal(
    await clientKey("Jefe", "what do ya want for nothing?"),
    "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843",
  );
});

test("clientKey depends on the salt and never contains the IP", async () => {
  const a = await clientKey("salt-a", "203.0.113.9");
  const b = await clientKey("salt-b", "203.0.113.9");
  assert.notEqual(a, b);
  assert.match(a, /^[0-9a-f]{64}$/);
  assert.ok(!a.includes("203"));
});
