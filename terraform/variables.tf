variable "cloudflare_account_id" {
  type        = string
  description = "Cloudflare account that owns the tunnel, the Workers and the R2 bucket."
}

variable "cloudflare_zone_id" {
  type        = string
  description = "Zone id of qoo.gy (DNS record, Worker routes)."
}

variable "cloudflare_api_token" {
  type        = string
  sensitive   = true
  description = "Token used by this configuration. See README.md for the permissions it needs."
}

variable "deploy_version" {
  type        = string
  default     = "v1"
  description = "Part of the decision cache key (DEPLOY_VERSION). Changing it empties the cache: bump it with a new model or label set."
}

variable "status_commit" {
  type        = string
  default     = "unknown"
  description = "Commit reported by /status.json (STATUS_COMMIT)."
}

variable "status_model" {
  type        = string
  default     = "unknown"
  description = "Model name reported by /status.json (STATUS_MODEL)."
}

variable "maintenance" {
  type        = string
  default     = "0"
  description = "\"1\" makes /api/* and /ingest answer 503 {\"error\": \"maintenance\"}; the landing stays up."

  validation {
    condition     = contains(["0", "1"], var.maintenance)
    error_message = "maintenance must be \"0\" or \"1\"."
  }
}

variable "cache_ttl" {
  type        = number
  default     = 86400
  description = "Lifetime in seconds of a cached decision (CACHE_TTL)."

  validation {
    condition     = var.cache_ttl > 0 && floor(var.cache_ttl) == var.cache_ttl
    error_message = "cache_ttl must be a whole number of seconds greater than 0."
  }
}

variable "debug_cache_header" {
  type        = string
  default     = "0"
  description = "\"1\" adds x-s1d1-cache: HIT or MISS to the cached API answers (DEBUG_CACHE_HEADER), for the smoke tests; off by default."

  validation {
    condition     = contains(["0", "1"], var.debug_cache_header)
    error_message = "debug_cache_header must be \"0\" or \"1\"."
  }
}

variable "origin_requires_auth" {
  type        = string
  default     = "0"
  description = "\"1\" when the origin gates decisions behind a token (ORIGIN_REQUIRES_AUTH): the cache key holds no Authorization, so the site Worker then neither reads nor fills the cache."

  validation {
    condition     = contains(["0", "1"], var.origin_requires_auth)
    error_message = "origin_requires_auth must be \"0\" or \"1\"."
  }
}

# ---------------------------------------------------------------------------------------------------------------------
# Google Cloud
# ---------------------------------------------------------------------------------------------------------------------
variable "gcp_project" {
  type        = string
  description = "Id of the Google Cloud project. It also names the raw bucket (\"<project>-s1d1-raw\"), since bucket names are global."

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.gcp_project))
    error_message = "gcp_project must be a project id (6 to 30 characters: lower-case letters, digits, hyphens)."
  }
}

variable "gcp_region" {
  type        = string
  default     = "europe-west1"
  description = "Region of the raw bucket, Cloud Run, Artifact Registry, the secrets and the Eventarc trigger (a Cloud Storage trigger must be in the bucket's region)."
}

variable "billing_account" {
  type        = string
  description = "Id of the billing account that pays the project (form 000000-000000-000000), for the budget."

  validation {
    condition     = can(regex("^[0-9A-F]{6}-[0-9A-F]{6}-[0-9A-F]{6}$", var.billing_account))
    error_message = "billing_account must look like 01A2B3-C4D5E6-F7A8B9."
  }
}

variable "budget_amount" {
  type        = number
  default     = 5
  description = "Monthly budget in whole units of budget_currency. Alerts only: a budget never stops the spending."
}

variable "budget_currency" {
  type        = string
  default     = "EUR"
  description = "ISO 4217 code. It must be the currency of the billing account, or the API refuses the budget."
}

variable "anonymizer_image" {
  type        = string
  default     = null
  nullable    = true
  description = "Image of the anonymizer, by digest: <region>-docker.pkg.dev/<project>/s1d1/anonymizer@sha256:<64 hex>, built by CI from this repo and pushed to Artifact Registry. Null (the default, for the first apply) creates neither the Cloud Run service nor the Eventarc trigger nor their IAM bindings."

  validation {
    condition     = var.anonymizer_image == null || can(regex("^[a-z0-9][a-z0-9./_-]*(:[A-Za-z0-9._-]{1,128})?@sha256:[0-9a-f]{64}$", var.anonymizer_image))
    error_message = "anonymizer_image must be null or given by digest (name@sha256:<64 hex>), so that a deployment is exactly the image built by CI from this repo."
  }
}

variable "github_repository" {
  type        = string
  default     = "scombat/s1d1-alliance"
  description = "GitHub repository (owner/name) whose main branch may push images to Artifact Registry."

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "github_repository must be owner/name."
  }
}

variable "github_repository_owner_id" {
  type        = string
  default     = ""
  description = "Optional numeric id of the GitHub owner (https://api.github.com/users/<owner>, field id). When set, the identity provider also requires it, so that a deleted and re-created account with the same name cannot use the federation."

  validation {
    condition     = can(regex("^[0-9]*$", var.github_repository_owner_id))
    error_message = "github_repository_owner_id must be digits."
  }
}

variable "terraform_service_account" {
  type        = string
  description = "Email of the service account HCP Terraform runs as (same value as TFC_GCP_RUN_SERVICE_ACCOUNT_EMAIL). It gets roles/iam.serviceAccountUser on the runtime accounts it attaches, and only on those."
}
