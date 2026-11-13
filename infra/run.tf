resource "google_service_account" "api" {
  account_id   = "folio-api"
  display_name = "Folio API runtime"
}

resource "google_secret_manager_secret_iam_member" "api_database_url" {
  secret_id = google_secret_manager_secret.database_url.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}

resource "google_secret_manager_secret_iam_member" "api_openai" {
  secret_id = google_secret_manager_secret.openai_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}

locals {
  plain_env = {
    FRONTEND_ORIGIN      = var.frontend_origin
    FIREBASE_USE_ADC     = "1"
    GOOGLE_CLOUD_PROJECT = var.project_id
  }
  secret_env = {
    DATABASE_URL   = google_secret_manager_secret.database_url.secret_id
    OPENAI_API_KEY = google_secret_manager_secret.openai_api_key.secret_id
  }
}

resource "google_cloud_run_v2_service" "api" {
  name                = "folio-api"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account = google_service_account.api.email

    scaling {
      min_instance_count = 0
      max_instance_count = 3
    }

    vpc_access {
      egress = "PRIVATE_RANGES_ONLY"
      network_interfaces {
        network    = google_compute_network.folio.id
        subnetwork = google_compute_subnetwork.run.id
      }
    }

    containers {
      image = var.api_image

      dynamic "env" {
        for_each = local.plain_env
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = local.secret_env
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image, client, client_version]
  }

  depends_on = [
    google_project_service.enabled,
    google_secret_manager_secret_iam_member.api_database_url,
    google_secret_manager_secret_iam_member.api_openai,
    google_secret_manager_secret_version.database_url,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  name     = google_cloud_run_v2_service.api.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_job" "migrate" {
  name                = "folio-migrate"
  location            = var.region
  deletion_protection = false

  template {
    template {
      service_account = google_service_account.api.email
      max_retries     = 0

      vpc_access {
        egress = "PRIVATE_RANGES_ONLY"
        network_interfaces {
          network    = google_compute_network.folio.id
          subnetwork = google_compute_subnetwork.run.id
        }
      }

      containers {
        image   = var.api_image
        command = ["python", "-m", "app.migrate"]

        env {
          name = "DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.database_url.secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].template[0].containers[0].image, client, client_version]
  }

  depends_on = [google_project_service.enabled, google_secret_manager_secret_iam_member.api_database_url]
}
