# Workload Identity Federation: the GitHub Actions workflows of this repository's main branch push the anonymizer
# image to Artifact Registry without any stored key.

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github"
  display_name              = "GitHub Actions"
  description               = "OIDC tokens of GitHub Actions"

  depends_on = [google_project_service.api]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-main"
  display_name                       = "GitHub, main branch"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }

  # Only this repository, only its main branch (a token minted from a pull request or another branch is refused).
  # Optionally also the numeric owner id, which survives a rename or a re-creation of the account name.
  attribute_condition = join(" && ", compact([
    "assertion.repository == \"${var.github_repository}\"",
    "assertion.ref == \"refs/heads/main\"",
    var.github_repository_owner_id == "" ? "" : "assertion.repository_owner_id == \"${var.github_repository_owner_id}\"",
  ]))

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "ci_publisher" {
  account_id   = "ci-publisher"
  display_name = "s1d1 CI: pushes images to Artifact Registry"

  depends_on = [google_project_service.api]
}

# It can write to the s1d1 repository and nowhere else.
resource "google_artifact_registry_repository_iam_member" "ci_publisher_writes" {
  project    = google_artifact_registry_repository.s1d1.project
  location   = google_artifact_registry_repository.s1d1.location
  repository = google_artifact_registry_repository.s1d1.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.ci_publisher.email}"
}

# The federated identities of this repository may act as ci-publisher.
resource "google_service_account_iam_member" "ci_publisher_from_github" {
  service_account_id = google_service_account.ci_publisher.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repository}"
}
