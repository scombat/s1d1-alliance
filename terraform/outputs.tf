output "tunnel_token" {
  description = "Token for `cloudflared tunnel run` on the nodes."
  value       = data.cloudflare_zero_trust_tunnel_cloudflared_token.s1d1.token
  sensitive   = true
}

output "community_token" {
  description = "Bearer token of the community node (S1D1_CAPTURE_TOKEN) and of the ingest Worker (COMMUNITY_TOKEN)."
  value       = random_password.community_token.result
  sensitive   = true
}

output "r2_access_key_id" {
  description = "S3 access key id for s1d1-clean: the id of the API token."
  value       = cloudflare_api_token.r2_clean.id
  sensitive   = true
}

output "r2_secret_access_key" {
  description = "S3 secret access key for s1d1-clean: the SHA-256 of the API token value."
  value       = sha256(cloudflare_api_token.r2_clean.value)
  sensitive   = true
}

output "r2_endpoint" {
  description = "S3 endpoint of the EU jurisdiction (not a secret)."
  value       = "https://${var.cloudflare_account_id}.eu.r2.cloudflarestorage.com"
}
