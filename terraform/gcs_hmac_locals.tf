# Placeholders for the three values the ingest Worker needs from the raw GCS bucket.
# Task 10 (gcp.tf) creates the bucket and the HMAC key of the `ingest-writer` service account and defines these
# locals from them: replaced in gcp.tf by Task 10. When that happens, delete this file (a local cannot be
# defined twice).
locals {
  raw_bucket_name = ""
  gcs_hmac_id     = ""
  gcs_hmac_secret = ""
}
