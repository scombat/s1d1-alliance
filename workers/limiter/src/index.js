// Limiter Worker: holds the Limiter Durable Object that the site and ingest Workers ask before serving a client
// (workers/site/src/limit.js, workers/ingest/src/limit.js). One instance per client and scope, named by the caller
// with an HMAC of the client IP: the IP itself never reaches this Worker. The counters live in memory only, nothing
// is written to storage; an instance left idle is evicted with its counters, which only ever happens to a client
// that has stopped sending. The Worker has no route and no workers.dev address: only the bindings reach it.
import { DurableObject } from "cloudflare:workers";
import { hit } from "./windows.js";

export class Limiter extends DurableObject {
  windows = new Map();

  /** RPC from the site and ingest Workers: {ok: true} or {ok: false, retryAfter}. */
  hit(rules) {
    return hit(this.windows, rules, Date.now());
  }
}

export default {
  fetch() {
    return new Response(null, { status: 404 });
  },
};
