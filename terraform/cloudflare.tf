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
# No zone ruleset here, on purpose. The /api rate limit is the API_BURST binding of the site Worker (workers.tf).
# The Free plan allows a single rate limiting rule per zone, and the zone's `http_ratelimit` entrypoint ruleset
# already exists and holds the rule of another subdomain. Declaring a `cloudflare_ruleset` for that phase fails
# (error 20217) and importing the entrypoint would put someone else's rule under this state: never do either.
# workers/site/test/terraform.test.js fails the build if a cloudflare_ruleset resource appears in terraform/*.tf.
# ---------------------------------------------------------------------------------------------------------------------

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
