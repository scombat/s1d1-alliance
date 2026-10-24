variable "cloudflare_account_id" {
  type        = string
  description = "Cloudflare account that owns the tunnel, the Workers and the R2 bucket."
}

variable "cloudflare_zone_id" {
  type        = string
  description = "Zone id of qoo.gy (DNS record, Worker routes, rate limit ruleset)."
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

variable "tantive_image" {
  type        = string
  default     = "unknown"
  description = "Tantive image (tag and digest) reported by /status.json (STATUS_TANTIVE_IMAGE)."
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
  description = "Image of the anonymizer, by digest: <region>-docker.pkg.dev/<project>/s1d1/anonymizer@sha256:<64 hex>. Cloud Run takes images from Artifact Registry (the CI pushes it there)."

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9./_-]*(:[A-Za-z0-9._-]{1,128})?@sha256:[0-9a-f]{64}$", var.anonymizer_image))
    error_message = "anonymizer_image must be given by digest (name@sha256:<64 hex>), so that a deployment is exactly the image that was scanned and signed."
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
