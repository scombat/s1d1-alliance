locals {
  hostname = "s1d1.qoo.gy"
}

# ---------------------------------------------------------------------------------------------------------------------
# DNS: the hostname points at the tunnel, proxied. Workers routes sit in front of it.
# ---------------------------------------------------------------------------------------------------------------------
resource "cloudflare_dns_record" "s1d1" {
  zone_id = var.cloudflare_zone_id
  name    = "s1d1"
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.s1d1.id}.cfargotunnel.com"
  proxied = true
  ttl     = 1 # automatic
  comment = "s1d1 community instance: Cloudflare Tunnel (managed by Terraform)"
}

# ---------------------------------------------------------------------------------------------------------------------
# Routes. The more specific route wins: /ingest goes to the ingest Worker, everything else to the site Worker.
# cloudflare_workers_route 5.28.1 has no fail open / fail closed attribute: see README.md ("Manual steps").
# ---------------------------------------------------------------------------------------------------------------------
resource "cloudflare_workers_route" "site" {
  zone_id = var.cloudflare_zone_id
  pattern = "${local.hostname}/*"
  script  = cloudflare_workers_script.site.script_name
}

resource "cloudflare_workers_route" "ingest" {
  zone_id = var.cloudflare_zone_id
  pattern = "${local.hostname}/ingest"
  script  = cloudflare_workers_script.ingest.script_name
}

# ---------------------------------------------------------------------------------------------------------------------
# Rate limit: one rule (the Free plan allows one), 100 requests per 10 s per IP on /api/*, blocked for 10 s.
# cf.colo.id is mandatory in the API and implicit in the dashboard: the count is per IP and per data centre.
# ---------------------------------------------------------------------------------------------------------------------
resource "cloudflare_ruleset" "rate_limit" {
  zone_id     = var.cloudflare_zone_id
  name        = "s1d1 API rate limit"
  description = "100 requests per 10 s per IP on /api/*, blocked for 10 s"
  kind        = "zone"
  phase       = "http_ratelimit"

  rules = [
    {
      ref         = "s1d1_api_rate_limit"
      description = "API: 100 requests / 10 s / IP"
      expression  = "starts_with(http.request.uri.path, \"/api/\")"
      action      = "block"
      enabled     = true
      ratelimit = {
        characteristics     = ["cf.colo.id", "ip.src"]
        period              = 10
        requests_per_period = 100
        mitigation_timeout  = 10
      }
    },
  ]
}

# ---------------------------------------------------------------------------------------------------------------------
# Secret shared by the community node (S1D1_CAPTURE_TOKEN) and the ingest Worker (COMMUNITY_TOKEN).
# ---------------------------------------------------------------------------------------------------------------------
resource "random_password" "community_token" {
  length  = 48
  special = false
}

# ---------------------------------------------------------------------------------------------------------------------
# R2: the clean bucket, EU jurisdiction, and a token that only reaches this bucket (for the anonymizer).
# ---------------------------------------------------------------------------------------------------------------------
resource "cloudflare_r2_bucket" "clean" {
  account_id   = var.cloudflare_account_id
  name         = "s1d1-clean"
  jurisdiction = "eu"
}

# The anonymizer only writes (PutObject of clean/...); it never reads, lists or deletes. So the token gets the write
# group alone, not "Item Read". R2 has no write-only object permission: "Item Write" is the dashboard's "Object Read &
# Write" and also allows reading and listing the objects of this bucket. The bucket scope below is the real limit.
data "cloudflare_api_token_permission_groups_list" "r2_item_write" {
  name = urlencode("Workers R2 Storage Bucket Item Write")
}

resource "cloudflare_api_token" "r2_clean" {
  name = "s1d1-clean object write (anonymizer)"

  policies = [
    {
      effect = "allow"
      permission_groups = [
        { id = data.cloudflare_api_token_permission_groups_list.r2_item_write.result[0].id },
      ]
      # Bucket-scoped resource key: <account>_<jurisdiction>_<bucket>.
      resources = jsonencode({
        "com.cloudflare.edge.r2.bucket.${var.cloudflare_account_id}_${cloudflare_r2_bucket.clean.jurisdiction}_${cloudflare_r2_bucket.clean.name}" = "*"
      })
    },
  ]
}
