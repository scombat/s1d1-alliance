# Google Cloud half of the instance: the raw bucket (3-day buffer), the anonymizer on Cloud Run, the Eventarc trigger
# that calls it, and the registry its image lives in. Budget: budget.tf. CI identity: wif.tf.
#
#   ingest Worker --HMAC--> raw bucket --object finalized--> Eventarc --OIDC--> anonymizer --S3--> R2 s1d1-clean
#
# The first apply runs with anonymizer_image = null: the registry, the federation, the buckets and the Workers exist,
# the anonymizer does not (no image to run yet). The Cloud Run service, its invoker binding, the Eventarc trigger and
# the anonymizer's IAM bindings are created by the apply that sets the image (docs/operations.md, section 3).

locals {
  anonymizer_enabled = var.anonymizer_image != null

  raw_bucket_name = google_storage_bucket.raw.name
  gcs_hmac_id     = google_storage_hmac_key.ingest_writer.access_id
  gcs_hmac_secret = google_storage_hmac_key.ingest_writer.secret

  # The anonymizer's R2 credentials: the same values as the outputs of the same names (cloudflare.tf).
  r2_endpoint          = "https://${var.cloudflare_account_id}.eu.r2.cloudflarestorage.com"
  r2_access_key_id     = cloudflare_api_token.r2_clean.id
  r2_secret_access_key = sha256(cloudflare_api_token.r2_clean.value)
}

data "google_project" "this" {
  project_id = var.gcp_project

  depends_on = [google_project_service.api]
}

# The Cloud Storage service agent (the identity that publishes bucket events to Pub/Sub).
data "google_storage_project_service_account" "gcs" {
  project = var.gcp_project

  depends_on = [google_project_service.api]
}

# ---------------------------------------------------------------------------------------------------------------------
# APIs. Not disabled on destroy: other things in the project may use them.
# Bootstrap: serviceusage and cloudresourcemanager must already be on for this resource to work (README).
# ---------------------------------------------------------------------------------------------------------------------
locals {
  gcp_apis = [
    "run.googleapis.com",
    "eventarc.googleapis.com",
    "pubsub.googleapis.com",
    "storage.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
    "billingbudgets.googleapis.com",
    "cloudresourcemanager.googleapis.com",
  ]
}

resource "google_project_service" "api" {
  for_each = toset(local.gcp_apis)

  project            = var.gcp_project
  service            = each.value
  disable_on_destroy = false
}

# ---------------------------------------------------------------------------------------------------------------------
# Service accounts: one per job, each with the one role it needs.
# ---------------------------------------------------------------------------------------------------------------------
resource "google_service_account" "ingest_writer" {
  account_id   = "ingest-writer"
  display_name = "s1d1 ingest Worker: writes raw captures (HMAC key)"

  depends_on = [google_project_service.api]
}

resource "google_service_account" "anonymizer" {
  account_id   = "anonymizer"
  display_name = "s1d1 anonymizer: reads the raw bucket, reads the R2 secrets"

  depends_on = [google_project_service.api]
}

resource "google_service_account" "eventarc_invoker" {
  account_id   = "eventarc-invoker"
  display_name = "s1d1 Eventarc trigger: receives bucket events and calls the anonymizer"

  depends_on = [google_project_service.api]
}

# ---------------------------------------------------------------------------------------------------------------------
# Raw bucket: a buffer, not an archive. Objects are deleted at 3 days; nobody reads them but the anonymizer.
# ---------------------------------------------------------------------------------------------------------------------
resource "google_storage_bucket" "raw" {
  name                        = "${var.gcp_project}-s1d1-raw"
  location                    = var.gcp_region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  # Without this a deleted object stays recoverable for 7 days (the default soft-delete window), which would turn the
  # 3-day buffer into 10 days of raw prompts.
  soft_delete_policy {
    retention_duration_seconds = 0
  }

  lifecycle_rule {
    condition {
      age = 3
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.api]
}

# The ingest Worker creates objects, nothing else (it can neither read, list, overwrite nor delete).
resource "google_storage_bucket_iam_member" "ingest_writer_creates" {
  bucket = google_storage_bucket.raw.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.ingest_writer.email}"
}

# The anonymizer reads objects of this bucket, nothing else.
resource "google_storage_bucket_iam_member" "anonymizer_reads" {
  count = local.anonymizer_enabled ? 1 : 0

  bucket = google_storage_bucket.raw.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.anonymizer.email}"
}

# S3-compatible key of ingest-writer, used by the Worker (SigV4 on storage.googleapis.com). The secret lands in the
# Terraform state (HCP, encrypted at rest) and in the Worker binding: nowhere else.
resource "google_storage_hmac_key" "ingest_writer" {
  service_account_email = google_service_account.ingest_writer.email
}

# ---------------------------------------------------------------------------------------------------------------------
# Secrets: the R2 credentials of the anonymizer. Stored in the region (no replication outside it).
# ---------------------------------------------------------------------------------------------------------------------
resource "google_secret_manager_secret" "r2_access_key_id" {
  secret_id = "r2-access-key-id"

  replication {
    user_managed {
      replicas {
        location = var.gcp_region
      }
    }
  }

  depends_on = [google_project_service.api]
}

resource "google_secret_manager_secret_version" "r2_access_key_id" {
  secret      = google_secret_manager_secret.r2_access_key_id.id
  secret_data = local.r2_access_key_id
}

resource "google_secret_manager_secret" "r2_secret_access_key" {
  secret_id = "r2-secret-access-key"

  replication {
    user_managed {
      replicas {
        location = var.gcp_region
      }
    }
  }

  depends_on = [google_project_service.api]
}

resource "google_secret_manager_secret_version" "r2_secret_access_key" {
  secret      = google_secret_manager_secret.r2_secret_access_key.id
  secret_data = local.r2_secret_access_key
}

resource "google_secret_manager_secret_iam_member" "anonymizer_r2_access_key_id" {
  count = local.anonymizer_enabled ? 1 : 0

  secret_id = google_secret_manager_secret.r2_access_key_id.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.anonymizer.email}"
}

resource "google_secret_manager_secret_iam_member" "anonymizer_r2_secret_access_key" {
  count = local.anonymizer_enabled ? 1 : 0

  secret_id = google_secret_manager_secret.r2_secret_access_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.anonymizer.email}"
}

# ---------------------------------------------------------------------------------------------------------------------
# Artifact Registry: where the CI pushes the anonymizer image (wif.tf) and where Cloud Run pulls it.
# No cleanup policy: a DELETE rule by age would delete the deployed image when no new one was pushed for that long,
# and Cloud Run could then not start a new instance. A KEEP rule alone deletes nothing, so none is set. Old images are
# deleted by hand, never the digest in images.auto.tfvars (terraform/README.md, "Artifact Registry").
# ---------------------------------------------------------------------------------------------------------------------
resource "google_artifact_registry_repository" "s1d1" {
  location      = var.gcp_region
  repository_id = "s1d1"
  format        = "DOCKER"
  description   = "s1d1-alliance images (anonymizer)"

  depends_on = [google_project_service.api]
}

# ---------------------------------------------------------------------------------------------------------------------
# The anonymizer on Cloud Run.
#  - ingress INTERNAL_ONLY: the internet cannot reach it, not even with a valid token. Eventarc (Pub/Sub push) is
#    allowed by Cloud Run for "internal" ingress when it is in the same project and uses the default run.app URL, see
#    https://docs.cloud.google.com/run/docs/securing/ingress . So default_uri_disabled stays false.
#  - no allUsers / allAuthenticatedUsers binding: only eventarc-invoker may call it (below).
#  - one request per instance (the models are CPU-bound), at most 2 instances, none when idle.
#  - 4 GiB: the two GLiNER encoders are about 0.7 GB (base) and 1.1 GB (multilingual) of float32 weights, plus torch and
#    the windows being scored; 2 GiB does not hold them. README.md of the anonymizer gives no measured RSS: lower this
#    only after reading the real peak in Cloud Run's metrics.
#  - 3600 s, the maximum of Cloud Run: a 65 536-character prompt takes about 10 s on a laptop, more on a vCPU, and
#    a batch holds up to 500 lines.
# ---------------------------------------------------------------------------------------------------------------------
resource "google_cloud_run_v2_service" "anonymizer" {
  count = local.anonymizer_enabled ? 1 : 0

  name                = "s1d1-anonymizer"
  location            = var.gcp_region
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"
  deletion_protection = false

  template {
    service_account                  = google_service_account.anonymizer.email
    timeout                          = "3600s"
    max_instance_request_concurrency = 1

    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }

    containers {
      image = var.anonymizer_image

      resources {
        # Measured at the first real run (2026-10-25): the two GLiNER models in fp32 plus torch peak at 4.1 to 4.6 GiB,
        # so 4 GiB was killed (signal 9) and retried. Cloud Run requires at least 2 vCPU above 4 GiB.
        limits = {
          cpu    = "2"
          memory = "8Gi"
        }
        cpu_idle          = true # CPU only while a request runs
        startup_cpu_boost = true # the models load on the first event
      }

      env {
        name  = "RAW_BUCKET"
        value = google_storage_bucket.raw.name
      }
      env {
        name  = "R2_ENDPOINT"
        value = local.r2_endpoint
      }
      env {
        name  = "R2_BUCKET"
        value = cloudflare_r2_bucket.clean.name
      }
      env {
        name  = "GLINER_MODEL_DIRS"
        value = "/model/knowledgator,/model/urchade" # the image's default, written down so that it is visible here
      }
      env {
        name  = "GLINER_THRESHOLD"
        value = "0.5"
      }
      env {
        name = "R2_ACCESS_KEY_ID"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.r2_access_key_id.secret_id
            version = google_secret_manager_secret_version.r2_access_key_id.version
          }
        }
      }
      env {
        name = "R2_SECRET_ACCESS_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.r2_secret_access_key.secret_id
            version = google_secret_manager_secret_version.r2_secret_access_key.version
          }
        }
      }
    }
  }

  depends_on = [
    google_project_service.api,
    google_secret_manager_secret_iam_member.anonymizer_r2_access_key_id,
    google_secret_manager_secret_iam_member.anonymizer_r2_secret_access_key,
    google_storage_bucket_iam_member.anonymizer_reads,
  ]
}

# The only principal that may invoke the service.
resource "google_cloud_run_v2_service_iam_member" "eventarc_invokes" {
  count = local.anonymizer_enabled ? 1 : 0

  project  = google_cloud_run_v2_service.anonymizer[0].project
  location = google_cloud_run_v2_service.anonymizer[0].location
  name     = google_cloud_run_v2_service.anonymizer[0].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.eventarc_invoker.email}"
}

# ---------------------------------------------------------------------------------------------------------------------
# Eventarc: object finalized in the raw bucket -> POST / on the anonymizer, with an OIDC token of eventarc-invoker.
# ---------------------------------------------------------------------------------------------------------------------

# The GCS service agent publishes the bucket events to the Pub/Sub topic that Eventarc creates for the trigger.
# Created by the first apply already (it does not depend on the image), so that it has propagated when the trigger is
# created; a trigger created right after it may still be refused until it does (a second apply then succeeds).
resource "google_project_iam_member" "gcs_agent_publishes" {
  project = var.gcp_project
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${data.google_storage_project_service_account.gcs.email_address}"
}

# The identity of the trigger must be allowed to receive events.
resource "google_project_iam_member" "eventarc_invoker_receives" {
  count = local.anonymizer_enabled ? 1 : 0

  project = var.gcp_project
  role    = "roles/eventarc.eventReceiver"
  member  = "serviceAccount:${google_service_account.eventarc_invoker.email}"
}

# The two Google-managed service agents that Eventarc needs. Both were granted by hand at the first deployment
# (2026-10-25); Terraform now grants them, non-authoritatively, so a fresh install does not fail the same way.
# A service agent exists only once its API is enabled (and sometimes only after first use): hence the dependency on the
# API, and if an agent still does not exist, `gcloud beta services identity create --service=<api>` creates it
# (the provider's google_project_service_identity is beta-only and the google-beta provider is not used here).

# Eventarc's own agent: manages the trigger's Pub/Sub plumbing.
resource "google_project_iam_member" "eventarc_agent" {
  project = var.gcp_project
  role    = "roles/eventarc.serviceAgent"
  member  = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-eventarc.iam.gserviceaccount.com"

  depends_on = [google_project_service.api["eventarc.googleapis.com"]]
}

# Pub/Sub's agent: signs the OIDC token of the push to Cloud Run on behalf of eventarc-invoker. Scoped to that one
# service account: a project-level grant would let the agent impersonate every account of the project.
resource "google_service_account_iam_member" "pubsub_agent_token_creator" {
  service_account_id = google_service_account.eventarc_invoker.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-pubsub.iam.gserviceaccount.com"

  depends_on = [google_project_service.api["pubsub.googleapis.com"]]
}

resource "google_eventarc_trigger" "raw_finalized" {
  count = local.anonymizer_enabled ? 1 : 0

  name     = "s1d1-raw-finalized"
  location = var.gcp_region # the bucket's region, lower case: the bucket reports its location in upper case (EUROPE-WEST1), which Eventarc rejects as "not found"

  matching_criteria {
    attribute = "type"
    value     = "google.cloud.storage.object.v1.finalized"
  }
  matching_criteria {
    attribute = "bucket"
    value     = google_storage_bucket.raw.name
  }

  destination {
    cloud_run_service {
      service = google_cloud_run_v2_service.anonymizer[0].name
      region  = google_cloud_run_v2_service.anonymizer[0].location
      path    = "/"
    }
  }

  service_account = google_service_account.eventarc_invoker.email

  # No retry_policy: the provider accepts only max_attempts = 1 there, and Eventarc's own default (Pub/Sub retry with
  # a 10 s to 600 s backoff, messages kept 24 h) is what we want. See README.md ("Retries").

  depends_on = [
    google_project_iam_member.gcs_agent_publishes,
    google_project_iam_member.eventarc_invoker_receives,
    google_project_iam_member.eventarc_agent,
    google_service_account_iam_member.pubsub_agent_token_creator,
    google_cloud_run_v2_service_iam_member.eventarc_invokes,
  ]
}
