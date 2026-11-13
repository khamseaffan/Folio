resource "random_password" "database" {
  length  = 32
  special = false
}

resource "google_sql_database_instance" "folio" {
  name                = "folio"
  region              = var.region
  database_version    = "POSTGRES_18"
  deletion_protection = true

  settings {
    edition           = "ENTERPRISE"
    tier              = "db-f1-micro"
    availability_type = "ZONAL"
    disk_size         = 10
    disk_autoresize   = true

    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.folio.id
      ssl_mode        = "ENCRYPTED_ONLY"
    }

    backup_configuration {
      enabled = true
    }
  }

  depends_on = [google_service_networking_connection.sql]
}

resource "google_sql_database" "folio" {
  name     = "folio"
  instance = google_sql_database_instance.folio.name
}

resource "google_sql_user" "folio" {
  name     = "folio"
  instance = google_sql_database_instance.folio.name
  password = random_password.database.result
}
