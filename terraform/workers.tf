# The two Workers are uploaded as ES modules without a build step: the main module and its imports are separate
# multipart parts (`content_file` and `files`). A part is named after its path relative to the Worker folder, so
# `import "./gcs.js"` and `import "../vendor/aws4fetch.js"` resolve inside the upload exactly as they do on disk.
#
# Redeploy on any file change: `content_sha256` here must equal the hash of `content_file` (the provider validates it,
# and refresh overwrites it with the hash of the deployed main module), so it can only cover index.js. The other
# files need no trigger of ours: for each `files` entry the provider computes `content_sha256` at plan time from its
# `content_file`, and for `assets.directory` it computes `asset_manifest_sha256` from the folder. Either one changing
# makes the plan an in-place update that uploads the whole Worker again (provider 5.28.1, workers_script/custom.go and
# assets.go). A `replace_triggered_by` would delete and recreate the script (and detach its route), so it is not used.
locals {
  compatibility_date = "2026-09-01"
  module_type        = "application/javascript+module"
}

# ---------------------------------------------------------------------------------------------------------------------
# Site Worker: static landing (../site), the /api allow-list relay, the decision cache, /status.json.
# ---------------------------------------------------------------------------------------------------------------------
resource "cloudflare_workers_script" "site" {
  account_id         = var.cloudflare_account_id
  script_name        = "s1d1-site"
  main_module        = "src/index.js"
  content_file       = "${path.module}/../workers/site/src/index.js"
  content_sha256     = filesha256("${path.module}/../workers/site/src/index.js")
  compatibility_date = local.compatibility_date

  files = {
    "src/api.js" = {
      content_file = "${path.module}/../workers/site/src/api.js"
      content_type = local.module_type
    }
    "src/cache.js" = {
      content_file = "${path.module}/../workers/site/src/cache.js"
      content_type = local.module_type
    }
  }

  assets = {
    directory = "${path.module}/../site"
    config = {
      # The Worker sees these paths before the static assets do (visit counter, status, API relay).
      run_worker_first = ["/", "/status.json", "/api/*"]
    }
  }

  bindings = [
    { name = "ASSETS", type = "assets" },
    { name = "VISITS", type = "analytics_engine", dataset = "s1d1_visits" },
    { name = "DEPLOY_VERSION", type = "plain_text", text = var.deploy_version },
    # Same-zone subrequest from a route Worker goes to the origin (the tunnel), not back to the Worker.
    { name = "ORIGIN", type = "plain_text", text = "https://${local.hostname}" },
    { name = "CACHE_TTL", type = "plain_text", text = tostring(var.cache_ttl) },
    { name = "MAINTENANCE", type = "plain_text", text = var.maintenance },
    { name = "STATUS_COMMIT", type = "plain_text", text = var.status_commit },
    { name = "STATUS_TANTIVE_IMAGE", type = "plain_text", text = var.tantive_image },
    { name = "STATUS_MODEL", type = "plain_text", text = var.status_model },
  ]
}

# ---------------------------------------------------------------------------------------------------------------------
# Ingest Worker: validates NDJSON batches and writes them to the raw GCS bucket.
# The three GCS values are locals defined in gcp.tf, from the raw bucket and the HMAC key of ingest-writer.
# ---------------------------------------------------------------------------------------------------------------------
resource "cloudflare_workers_script" "ingest" {
  account_id         = var.cloudflare_account_id
  script_name        = "s1d1-ingest"
  main_module        = "src/index.js"
  content_file       = "${path.module}/../workers/ingest/src/index.js"
  content_sha256     = filesha256("${path.module}/../workers/ingest/src/index.js")
  compatibility_date = local.compatibility_date

  files = {
    "src/gcs.js" = {
      content_file = "${path.module}/../workers/ingest/src/gcs.js"
      content_type = local.module_type
    }
    "src/validate.js" = {
      content_file = "${path.module}/../workers/ingest/src/validate.js"
      content_type = local.module_type
    }
    "vendor/aws4fetch.js" = {
      content_file = "${path.module}/../workers/ingest/vendor/aws4fetch.js"
      content_type = local.module_type
    }
  }

  bindings = [
    { name = "RAW_BUCKET", type = "plain_text", text = local.raw_bucket_name },
    { name = "GCS_HMAC_ID", type = "plain_text", text = local.gcs_hmac_id },
    { name = "GCS_HMAC_SECRET", type = "secret_text", text = local.gcs_hmac_secret },
    { name = "COMMUNITY_TOKEN", type = "secret_text", text = random_password.community_token.result },
    { name = "MAINTENANCE", type = "plain_text", text = var.maintenance },
  ]
}

# A Worker that is also reachable on workers.dev would bypass the zone's rate limit rule: keep both off the
# workers.dev subdomain, they are served by the routes only.
resource "cloudflare_workers_script_subdomain" "site" {
  account_id       = var.cloudflare_account_id
  script_name      = cloudflare_workers_script.site.script_name
  enabled          = false
  previews_enabled = false
}

resource "cloudflare_workers_script_subdomain" "ingest" {
  account_id       = var.cloudflare_account_id
  script_name      = cloudflare_workers_script.ingest.script_name
  enabled          = false
  previews_enabled = false
}
