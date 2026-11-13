resource "google_compute_network" "folio" {
  name                    = "folio"
  auto_create_subnetworks = false

  depends_on = [google_project_service.enabled]
}

resource "google_compute_subnetwork" "run" {
  name          = "folio-run"
  region        = var.region
  network       = google_compute_network.folio.id
  ip_cidr_range = "10.10.0.0/24"
}

# Private services access: the address range Cloud SQL's private IP is taken from.
resource "google_compute_global_address" "sql_range" {
  name          = "folio-sql-range"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 20
  network       = google_compute_network.folio.id
}

resource "google_service_networking_connection" "sql" {
  network                 = google_compute_network.folio.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.sql_range.name]
}
