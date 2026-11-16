# Where uploads and task files live. One private bucket, a folder per person.
resource "google_storage_bucket" "files" {
  name                        = "${var.project_id}-folio-files"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  # An upload that never finished is discarded after a day. Unfinished uploads are written under
  # pending/ and moved to the person's folder once the file row commits.
  lifecycle_rule {
    condition {
      age            = 1
      matches_prefix = ["pending/"]
    }
    action {
      type = "Delete"
    }
  }

  # The browser sends the bytes straight to the bucket with an address the API signed, because a
  # Cloud Run request body is limited to 32 MiB and an upload may be 50 MB.
  cors {
    origin          = [var.frontend_origin]
    method          = ["PUT", "GET", "HEAD"]
    response_header = ["Content-Type", "Content-Length"]
    max_age_seconds = 3600
  }

  depends_on = [google_project_service.enabled]
}

# The API stores, reads and deletes files. The worker writes task files. Research only reads.
resource "google_storage_bucket_iam_member" "api_files" {
  bucket = google_storage_bucket.files.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.api.email}"
}

resource "google_storage_bucket_iam_member" "worker_files" {
  bucket = google_storage_bucket.files.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_storage_bucket_iam_member" "research_files" {
  bucket = google_storage_bucket.files.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.research.email}"
}

# A signed upload address is signed by the API's own account, through the IAM Credentials API.
resource "google_service_account_iam_member" "api_signs_for_itself" {
  service_account_id = google_service_account.api.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.api.email}"
}
