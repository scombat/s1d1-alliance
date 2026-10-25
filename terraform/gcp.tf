# Google Cloud half of the instance: the raw bucket (3-day buffer), the anonymizer as a Cloud Run Job, the Cloud
# Scheduler job that starts it every 2 days, and the registry its image lives in. Budget: budget.tf. CI: wif.tf.
#
#   ingest Worker --HMAC--> raw bucket <--reads-- anonymizer job --S3--> R2 s1d1-clean
#                                                     ^ every 2 days: Cloud Scheduler --OAuth--> jobs.run
#
# The cost is bounded by the job, not by the traffic: each run draws at most anonymizer_max_prompts lines and stops at
# 55 minutes (docs/31 of the project). Until 2026-10-25 an Eventarc trigger ran the anonymizer on every raw object.
#
# The first apply runs with anonymizer_image = null: the registry, the federation, the buckets and the Workers exist,
# the anonymizer does not (no image to run yet). The job, its schedule and the anonymizer's IAM bindings are created
# by the apply that sets the image (docs/operations.md, section 3).

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

# ---------------------------------------------------------------------------------------------------------------------
# APIs. Not disabled on destroy: other things in the project may use them.
# Bootstrap: serviceusage and cloudresourcemanager must already be on for this resource to work (README).
# ---------------------------------------------------------------------------------------------------------------------
locals {
  gcp_apis = [
    "run.googleapis.com",
    "cloudscheduler.googleapis.com",
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

resource "google_service_account" "scheduler" {
  account_id   = "anonymizer-scheduler"
  display_name = "s1d1 Cloud Scheduler: starts the anonymizer job, nothing else"

  depends_on = [google_project_service.api]
}

# The account HCP runs as acts (iam.serviceAccounts.actAs) only as the runtime accounts Terraform attaches to a resource:
# anonymizer (identity of the Cloud Run Job) and anonymizer-scheduler (identity of the Cloud Scheduler job). ingest-writer is only the owner of
# an HMAC key and is attached to nothing. The HCP account itself is bootstrapped by hand (README.md, "Manual steps").
resource "google_service_account_iam_member" "terraform_acts_as_anonymizer" {
  service_account_id = google_service_account.anonymizer.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.terraform_service_account}"
}

resource "google_service_account_iam_member" "terraform_acts_as_scheduler" {
  service_account_id = google_service_account.scheduler.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.terraform_service_account}"
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

# The anonymizer reads and lists objects of this bucket, nothing else (it never deletes: the lifecycle rule does).
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
# The anonymizer as a Cloud Run Job (python -m anonymizer.batch, anonymizer/README.md).
#  - one task, no retry: a failed run waits for the next one (2 days later at most, within the 3-day buffer).
#  - timeout 3600 s; the job stops cleaning at MAX_SECONDS = 3300 by itself and writes what it kept, so the timeout
#    is only a backstop. Cost bound per run: 2 vCPU and 8 GiB for an hour at most.
#  - 8 GiB and 2 vCPU: measured at the first real run (2026-10-25), the two GLiNER models in fp32 plus torch peak at
#    4.1 to 4.6 GiB, so 4 GiB was killed (signal 9). Cloud Run requires at least 2 vCPU above 4 GiB.
#  - a job has no URL: nothing on the internet can reach it. Only anonymizer-scheduler may run it (below), and the
#    maintainer by hand (docs/operations.md, "Run the anonymizer by hand").
# ---------------------------------------------------------------------------------------------------------------------
resource "google_cloud_run_v2_job" "anonymizer" {
  count = local.anonymizer_enabled ? 1 : 0

  name                = "s1d1-anonymizer-job"
  location            = var.gcp_region
  deletion_protection = false

  template {
    task_count  = 1
    parallelism = 1

    template {
      service_account = google_service_account.anonymizer.email
      timeout         = "3600s"
      max_retries     = 0

      containers {
        image   = var.anonymizer_image
        command = ["python", "-m", "anonymizer.batch"]

        resources {
          limits = {
            cpu    = "2"
            memory = "8Gi"
          }
        }

        env {
          name  = "RAW_BUCKET"
          value = google_storage_bucket.raw.name
        }
        env {
          name  = "MAX_PROMPTS"
          value = tostring(var.anonymizer_max_prompts)
        }
        env {
          name  = "MAX_SECONDS"
          value = "3300"
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
  }

  depends_on = [
    google_project_service.api,
    google_service_account_iam_member.terraform_acts_as_anonymizer,
    google_secret_manager_secret_iam_member.anonymizer_r2_access_key_id,
    google_secret_manager_secret_iam_member.anonymizer_r2_secret_access_key,
    google_storage_bucket_iam_member.anonymizer_reads,
  ]
}

# The only principal that may run the job (roles/run.invoker holds run.jobs.run), on this job only.
resource "google_cloud_run_v2_job_iam_member" "scheduler_runs" {
  count = local.anonymizer_enabled ? 1 : 0

  project  = google_cloud_run_v2_job.anonymizer[0].project
  location = google_cloud_run_v2_job.anonymizer[0].location
  name     = google_cloud_run_v2_job.anonymizer[0].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

# ---------------------------------------------------------------------------------------------------------------------
# Cloud Scheduler: POST jobs/<name>:run on the Cloud Run Admin API with an OAuth token of anonymizer-scheduler.
# "0 3 */2 * *" runs on the odd days of the month at 03:00 UTC: two runs are never more than 2 days apart (31 -> 1 is
# 1 day, 27 Feb -> 1 Mar is 2), so every raw object is seen by at least one run before its 3-day deletion.
# No retry: a missed run is caught up by the next one; a raw object it would have seen may then be deleted unseen.
# ---------------------------------------------------------------------------------------------------------------------
resource "google_cloud_scheduler_job" "anonymizer" {
  count = local.anonymizer_enabled ? 1 : 0

  name      = "s1d1-anonymizer-every-2-days"
  region    = var.gcp_region
  schedule  = var.anonymizer_schedule
  time_zone = "Etc/UTC"

  retry_config {
    retry_count = 0
  }

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.gcp_project}/locations/${var.gcp_region}/jobs/${google_cloud_run_v2_job.anonymizer[0].name}:run"

    oauth_token {
      service_account_email = google_service_account.scheduler.email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  depends_on = [
    google_project_service.api,
    google_service_account_iam_member.terraform_acts_as_scheduler,
    google_cloud_run_v2_job_iam_member.scheduler_runs,
  ]
}
