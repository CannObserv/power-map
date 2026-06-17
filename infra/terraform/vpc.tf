resource "digitalocean_vpc" "power_map" {
  name   = "co-pm-vpc"
  region = var.region
}
