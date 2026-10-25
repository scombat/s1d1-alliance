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
  value       = local.r2_access_key_id
  sensitive   = true
}

output "r2_secret_access_key" {
  description = "S3 secret access key for s1d1-clean: the SHA-256 of the API token value."
  value       = local.r2_secret_access_key
  sensitive   = true
}

output "r2_endpoint" {
  description = "S3 endpoint of the EU jurisdiction (not a secret)."
  value       = local.r2_endpoint
  sensitive   = false # not a secret: the account id is not one and the endpoint is needed in clear by the anonymizer
}

# Google Cloud (none of these is a secret).
output "ci_workload_identity_provider" {
  description = "Value of `workload_identity_provider` for google-github-actions/auth in the repository's workflows."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "ci_publisher_service_account" {
  description = "Value of `service_account` for google-github-actions/auth: the identity that pushes the anonymizer image."
  value       = google_service_account.ci_publisher.email
}

output "artifact_registry_repository" {
  description = "Docker repository to push the anonymizer image to (image name: <this>/anonymizer)."
  value       = "${google_artifact_registry_repository.s1d1.location}-docker.pkg.dev/${var.gcp_project}/${google_artifact_registry_repository.s1d1.repository_id}"
}

output "anonymizer_job" {
  description = "Name of the anonymizer Cloud Run Job (null until anonymizer_image is set): gcloud run jobs execute <this> --region <gcp_region>."
  value       = one(google_cloud_run_v2_job.anonymizer[*].name)
}
