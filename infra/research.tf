locals {
  # Built from the project number for the same reason as worker_url.
  research_url = "https://folio-research-${data.google_project.current.number}.${var.region}.run.app"

  # What code needs to put a research run on its queue. The API and the worker both do it.
  research_env = {
    RESEARCH_QUEUE  = google_cloud_tasks_queue.research.id
    RESEARCH_URL    = local.research_url
    INVOKER_ACCOUNT = google_service_account.invoker.email
  }
}

resource "google_service_account" "research" {
  account_id   = "folio-research"
  display_name = "Folio research runtime"
}

resource "google_secret_manager_secret_iam_member" "research_database_url" {
  secret_id = google_secret_manager_secret.database_url.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.research.email}"
}

resource "google_secret_manager_secret_iam_member" "research_openai" {
  secret_id = google_secret_manager_secret.openai_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.research.email}"
}

resource "google_secret_manager_secret_iam_member" "research_tavily" {
  secret_id = google_secret_manager_secret.tavily_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.research.email}"
}

resource "google_cloud_tasks_queue" "research" {
  name     = "folio-research"
  location = var.region

  # Each run holds a Chrome, so this bounds memory across the service.
  rate_limits {
    max_concurrent_dispatches = 3
  }

  retry_config {
    max_attempts = 4
    min_backoff  = "10s"
    max_backoff  = "300s"
  }

  depends_on = [google_project_service.enabled]
}

resource "google_cloud_tasks_queue_iam_member" "api_enqueues_research" {
  name     = google_cloud_tasks_queue.research.name
  location = var.region
  role     = "roles/cloudtasks.enqueuer"
  member   = "serviceAccount:${google_service_account.api.email}"
}

resource "google_cloud_tasks_queue_iam_member" "worker_enqueues_research" {
  name     = google_cloud_tasks_queue.research.name
  location = var.region
  role     = "roles/cloudtasks.enqueuer"
  member   = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_service_account_iam_member" "api_acts_as_invoker" {
  service_account_id = google_service_account.invoker.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.api.email}"
}

resource "google_cloud_run_v2_service" "research" {
  name                = "folio-research"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account                  = google_service_account.research.email
    timeout                          = "1800s"
    execution_environment            = "EXECUTION_ENVIRONMENT_GEN2"
    max_instance_request_concurrency = 1

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
      image   = var.research_image
      command = ["sh", "-c"]
      args    = ["exec uvicorn app.research_worker:app --host 0.0.0.0 --port $${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'"]

      # Chrome needs more than the API does. Measure a real run, then adjust.
      resources {
        limits = {
          cpu    = "2"
          memory = "4Gi"
        }
      }

      dynamic "env" {
        for_each = local.plain_env
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = merge(local.secret_env, {
          TAVILY_API_KEY = google_secret_manager_secret.tavily_api_key.secret_id
        })
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
    google_secret_manager_secret_iam_member.research_database_url,
    google_secret_manager_secret_iam_member.research_openai,
    google_secret_manager_secret_iam_member.research_tavily,
    google_secret_manager_secret_version.database_url,
  ]
}

# No allUsers binding: only the invoker account can call the research service.
resource "google_cloud_run_v2_service_iam_member" "research_invoker" {
  name     = google_cloud_run_v2_service.research.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.invoker.email}"
}
