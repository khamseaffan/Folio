# The key that encrypts what an integration keeps for a person, such as the Google Drive token.
resource "google_kms_key_ring" "folio" {
  name     = "folio"
  location = var.region

  depends_on = [google_project_service.enabled]
}

resource "google_kms_crypto_key" "integrations" {
  name            = "integrations"
  key_ring        = google_kms_key_ring.folio.id
  rotation_period = "7776000s"

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_kms_crypto_key_iam_member" "api_integrations" {
  crypto_key_id = google_kms_crypto_key.integrations.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${google_service_account.api.email}"
}

# The value is added by hand: gcloud secrets versions add gemini-api-key
resource "google_secret_manager_secret" "gemini_api_key" {
  secret_id = "gemini-api-key"

  replication {
    auto {}
  }

  depends_on = [google_project_service.enabled]
}

resource "google_secret_manager_secret_iam_member" "api_gemini" {
  secret_id = google_secret_manager_secret.gemini_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}

# The value is added by hand: gcloud secrets versions add drive-client-secret
resource "google_secret_manager_secret" "drive_client_secret" {
  secret_id = "drive-client-secret"

  replication {
    auto {}
  }

  depends_on = [google_project_service.enabled]
}

resource "google_secret_manager_secret_iam_member" "api_drive_client_secret" {
  secret_id = google_secret_manager_secret.drive_client_secret.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}
