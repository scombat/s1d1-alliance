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
  # /status.json shows what the nodes are pinned to: the TANTIVE_IMAGE line of node/desired.env, never typed by hand.
  tantive_image = regex("(?m)^TANTIVE_IMAGE=(\\S+)$", file("${path.module}/../node/desired.env"))[0]
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

  # Workers Logs: our own console lines (they never carry request data) are kept, the platform's invocation logs are
  # off ON PURPOSE. They store per-request metadata at Cloudflare (request, response, client details), which would
  # contradict the privacy policy: the IP address and headers are never recorded by the service. Never enable them
  # from the dashboard: the next apply reverts it.
  # Every value the API returns is written out, `destinations = []` and the traces' sampling rate included: refresh
  # copies each non-null value the settings endpoint returns over the state (provider 5.28.1, workers_script/
  # resource.go, Read), so one left unset here would show as a change in every plan and redeploy the Worker.
  observability = {
    enabled            = true
    head_sampling_rate = 1
    logs = {
      enabled            = true
      invocation_logs    = false
      head_sampling_rate = 1
      persist            = true
      destinations       = []
    }
    traces = {
      enabled            = false
      head_sampling_rate = 1
      persist            = true
      destinations       = []
    }
  }

  files = {
    "src/api.js" = {
      content_file = "${path.module}/../workers/site/src/api.js"
      content_type = local.module_type
    }
    "src/cache.js" = {
      content_file = "${path.module}/../workers/site/src/cache.js"
      content_type = local.module_type
    }
    "src/limit.js" = {
      content_file = "${path.module}/../workers/site/src/limit.js"
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
    { name = "DEBUG_CACHE_HEADER", type = "plain_text", text = var.debug_cache_header },
    { name = "ORIGIN_REQUIRES_AUTH", type = "plain_text", text = var.origin_requires_auth },
    { name = "MAINTENANCE", type = "plain_text", text = var.maintenance },
    { name = "STATUS_COMMIT", type = "plain_text", text = var.status_commit },
    { name = "STATUS_TANTIVE_IMAGE", type = "plain_text", text = local.tantive_image },
    { name = "STATUS_MODEL", type = "plain_text", text = var.status_model },
    # Rate limit of /api/*, per client: the Limiter Durable Object of s1d1-limiter (below), named by an HMAC of the
    # client IP under LIMIT_SALT. There is no zone ruleset for it: see cloudflare.tf.
    { name = "LIMITER", type = "durable_object_namespace", class_name = "Limiter", script_name = cloudflare_workers_script.limiter.script_name },
    { name = "LIMIT_SALT", type = "secret_text", text = random_password.limit_salt.result },
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

  # Workers Logs: our own console lines (they never carry request data) are kept, the platform's invocation logs are
  # off ON PURPOSE. They store per-request metadata at Cloudflare (request, response, client details), which would
  # contradict the privacy policy: the IP address and headers are never recorded by the service. Never enable them
  # from the dashboard: the next apply reverts it.
  # Every value the API returns is written out, `destinations = []` and the traces' sampling rate included: refresh
  # copies each non-null value the settings endpoint returns over the state (provider 5.28.1, workers_script/
  # resource.go, Read), so one left unset here would show as a change in every plan and redeploy the Worker.
  observability = {
    enabled            = true
    head_sampling_rate = 1
    logs = {
      enabled            = true
      invocation_logs    = false
      head_sampling_rate = 1
      persist            = true
      destinations       = []
    }
    traces = {
      enabled            = false
      head_sampling_rate = 1
      persist            = true
      destinations       = []
    }
  }

  files = {
    "src/gcs.js" = {
      content_file = "${path.module}/../workers/ingest/src/gcs.js"
      content_type = local.module_type
    }
    "src/validate.js" = {
      content_file = "${path.module}/../workers/ingest/src/validate.js"
      content_type = local.module_type
    }
    "src/limit.js" = {
      content_file = "${path.module}/../workers/ingest/src/limit.js"
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
    # Per-client limits: the same Limiter Durable Object as the site Worker, in its own "ingest:" names.
    # workers/ingest/test/terraform.test.js checks that both stay declared.
    { name = "LIMITER", type = "durable_object_namespace", class_name = "Limiter", script_name = cloudflare_workers_script.limiter.script_name },
    { name = "LIMIT_SALT", type = "secret_text", text = random_password.limit_salt.result },
  ]
}

# ---------------------------------------------------------------------------------------------------------------------
# Limiter Worker: the Limiter Durable Object (SQLite-backed class, the only kind on the Workers Free plan; it writes
# nothing to storage, the counters live in memory). No route, no workers.dev address: only the LIMITER bindings of the
# two Workers above reach it. docs/31 of the project, workers/limiter.
#
# Migrations: the provider sends `migrations` from the configuration with every upload of this script (provider
# 5.28.1, workers_script/resource.go, Update), not only the first. The tag v1 creates the class; whether Cloudflare
# accepts the same v1 again on a later upload is not verified. This script is kept tiny and separate so that it is
# rarely uploaded again, and an upload of the site or ingest Worker never sends a migration. If a later apply of this
# script is refused on the migration tag, add `old_tag = "v1"` next to `new_tag`.
# ---------------------------------------------------------------------------------------------------------------------
resource "cloudflare_workers_script" "limiter" {
  account_id         = var.cloudflare_account_id
  script_name        = "s1d1-limiter"
  main_module        = "src/index.js"
  content_file       = "${path.module}/../workers/limiter/src/index.js"
  content_sha256     = filesha256("${path.module}/../workers/limiter/src/index.js")
  compatibility_date = local.compatibility_date

  # Same pinned Workers Logs as the two Workers above (this one logs nothing of its own).
  observability = {
    enabled            = true
    head_sampling_rate = 1
    logs = {
      enabled            = true
      invocation_logs    = false
      head_sampling_rate = 1
      persist            = true
      destinations       = []
    }
    traces = {
      enabled            = false
      head_sampling_rate = 1
      persist            = true
      destinations       = []
    }
  }

  files = {
    "src/windows.js" = {
      content_file = "${path.module}/../workers/limiter/src/windows.js"
      content_type = local.module_type
    }
  }

  migrations = {
    new_tag            = "v1"
    new_sqlite_classes = ["Limiter"]
  }
}

# The key of the HMAC that names a client in the limiter (workers/site/src/limit.js). Shared by the two Workers, never
# output: rotating it (taint) only resets the counters.
resource "random_password" "limit_salt" {
  length  = 48
  special = false
}

# A Worker that is also reachable on workers.dev would be reached without the hostname's route and its protections
# (maintenance, rate limits): keep both off the workers.dev subdomain, they are served by the routes only.
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

resource "cloudflare_workers_script_subdomain" "limiter" {
  account_id       = var.cloudflare_account_id
  script_name      = cloudflare_workers_script.limiter.script_name
  enabled          = false
  previews_enabled = false
}
