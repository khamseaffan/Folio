data "google_project" "current" {
  project_id = var.project_id

  depends_on = [google_project_service.enabled]
}

locals {
  # A service cannot read its own uri in its own environment, so the address is built from
  # the project number. Cloud Run serves every service at this address.
  worker_url = "https://folio-worker-${data.google_project.current.number}.${var.region}.run.app"
}

resource "google_service_account" "worker" {
  account_id   = "folio-worker"
  display_name = "Folio worker runtime"
}

resource "google_service_account" "invoker" {
  account_id   = "folio-invoker"
  display_name = "Folio caller of the worker"
}

resource "google_secret_manager_secret_iam_member" "worker_database_url" {
  secret_id = google_secret_manager_secret.database_url.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_secret_manager_secret_iam_member" "worker_openai" {
  secret_id = google_secret_manager_secret.openai_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_secret_manager_secret_iam_member" "worker_tavily" {
  secret_id = google_secret_manager_secret.tavily_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_cloud_tasks_queue" "runs" {
  name     = "folio-runs"
  location = var.region

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

resource "google_cloud_tasks_queue_iam_member" "worker_enqueues" {
  name     = google_cloud_tasks_queue.runs.name
  location = var.region
  role     = "roles/cloudtasks.enqueuer"
  member   = "serviceAccount:${google_service_account.worker.email}"
}

# Cloud Tasks only attaches the invoker's token to a task when the account that creates the
# task may act as the invoker.
resource "google_service_account_iam_member" "worker_acts_as_invoker" {
  service_account_id = google_service_account.invoker.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.worker.email}"
}

# Cloud Scheduler signs the call to the tick route with the invoker's identity. Its service
# agent is the one that asks Google for that token.
resource "google_service_account_iam_member" "scheduler_mints_invoker_token" {
  service_account_id = google_service_account.invoker.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-cloudscheduler.iam.gserviceaccount.com"

  depends_on = [google_project_service.enabled]
}

resource "google_cloud_run_v2_service" "worker" {
  name                = "folio-worker"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account = google_service_account.worker.email
    timeout         = "1800s"

    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }

    vpc_access {
      egress = "PRIVATE_RANGES_ONLY"
      network_interfaces {
        network    = google_compute_network.folio.id
        subnetwork = google_compute_subnetwork.run.id
      }
    }

    containers {
      image   = var.api_image
      command = ["sh", "-c"]
      args    = ["exec uvicorn app.worker:app --host 0.0.0.0 --port $${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'"]

      dynamic "env" {
        for_each = merge(local.plain_env, local.research_env, {
          RUN_QUEUE       = google_cloud_tasks_queue.runs.id
          WORKER_URL      = local.worker_url
          INVOKER_ACCOUNT = google_service_account.invoker.email
        })
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
    google_secret_manager_secret_iam_member.worker_database_url,
    google_secret_manager_secret_iam_member.worker_openai,
    google_secret_manager_secret_iam_member.worker_tavily,
    google_secret_manager_secret_version.database_url,
  ]
}

# No allUsers binding: only the invoker account can call the worker.
resource "google_cloud_run_v2_service_iam_member" "worker_invoker" {
  name     = google_cloud_run_v2_service.worker.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.invoker.email}"
}

resource "google_cloud_scheduler_job" "tick" {
  name             = "folio-tick"
  region           = var.region
  schedule         = "* * * * *"
  time_zone        = "Etc/UTC"
  attempt_deadline = "60s"

  # The next minute is the retry.
  retry_config {
    retry_count = 0
  }

  http_target {
    http_method = "POST"
    uri         = "${local.worker_url}/tick"

    oidc_token {
      service_account_email = google_service_account.invoker.email
      audience              = local.worker_url
    }
  }

  depends_on = [
    google_project_service.enabled,
    google_cloud_run_v2_service_iam_member.worker_invoker,
    google_service_account_iam_member.scheduler_mints_invoker_token,
  ]
}
