// Write to the raw bucket through the GCS S3-compatible XML API (HMAC key, SigV4, region "auto").
import { AwsClient } from "../vendor/aws4fetch.js";

const hex = (buffer) => [...new Uint8Array(buffer)].map((b) => b.toString(16).padStart(2, "0")).join("");

/** PUT `body` (a string) at `key` in env.RAW_BUCKET. Throws when the answer is not 2xx. */
export async function putRaw(env, key, body) {
  const client = new AwsClient({
    accessKeyId: env.GCS_HMAC_ID,
    secretAccessKey: env.GCS_HMAC_SECRET,
    service: "s3",
    region: "auto",
    retries: 1, // tantive retries a 503 later; a long back-off here would hold the request open
  });
  const bytes = new TextEncoder().encode(body);
  // The real payload hash rather than UNSIGNED-PAYLOAD (aws4fetch's S3 default): every S3-compatible server takes it.
  const sha256 = hex(await crypto.subtle.digest("SHA-256", bytes));
  const url = `https://storage.googleapis.com/${env.RAW_BUCKET}/${key.split("/").map(encodeURIComponent).join("/")}`;
  const res = await client.fetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/x-ndjson", "X-Amz-Content-Sha256": sha256 },
    body: bytes,
  });
  await res.body?.cancel();
  if (!res.ok) {
    // Status only: neither the response body nor the object body goes into the error.
    const error = new Error(`raw bucket write failed with status ${res.status}`);
    error.status = res.status;
    throw error;
  }
}
