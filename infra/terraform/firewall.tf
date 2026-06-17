resource "digitalocean_database_firewall" "power_map" {
  cluster_id = digitalocean_database_cluster.power_map.id

  # All callers are external (VM is not a DO droplet) — IP allowlist only.
  dynamic "rule" {
    for_each = var.allowed_external_ips
    content {
      type  = "ip_addr"
      value = rule.value
    }
  }
}
