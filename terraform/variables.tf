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
