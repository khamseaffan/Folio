resource "google_secret_manager_secret" "database_url" {
  secret_id = "database-url"

  replication {
    auto {}
  }

  depends_on = [google_project_service.enabled]
}

resource "google_secret_manager_secret_version" "database_url" {
  secret      = google_secret_manager_secret.database_url.id
  secret_data = "postgresql://folio:${random_password.database.result}@${google_sql_database_instance.folio.private_ip_address}:5432/folio?sslmode=require"
}

# The value is added by hand: gcloud secrets versions add openai-api-key
resource "google_secret_manager_secret" "openai_api_key" {
  secret_id = "openai-api-key"

  replication {
    auto {}
  }

  depends_on = [google_project_service.enabled]
}
