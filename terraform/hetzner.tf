# The community node: one small Hetzner Cloud ARM server (Ampere) that runs the generic compose of ../node through
# cloud-init. It has a public IP only to go OUT (tunnel, ghcr.io, raw.githubusercontent.com, apt); nothing can come IN:
# the firewall below has no inbound rule, there is no SSH key, and no port is published by the compose.
variable "hcloud_token" {
  type        = string
  sensitive   = true
  description = "API token (Read & Write) of the Hetzner Cloud project that holds the node."
}

variable "hcloud_location" {
  type        = string
  default     = "nbg1"
  description = "Hetzner location of the node. Limited to the EU locations on purpose (decision: the data stays in the EU): fsn1 (Falkenstein), nbg1 (Nuremberg), hel1 (Helsinki)."

  validation {
    condition     = contains(["fsn1", "nbg1", "hel1"], var.hcloud_location)
    error_message = "hcloud_location must be fsn1 (Falkenstein), nbg1 (Nuremberg) or hel1 (Helsinki): the node stays in the EU."
  }
}

variable "hcloud_server_type" {
  type        = string
  default     = "cax11"
  description = "Server type. Default cax11: Arm (Ampere), 2 vCPU, 4 GB RAM, 40 GB disk. If CAX (Arm) stays unavailable, use a shared x86 type with 4 GB: cx23 (2 vCPU, 40 GB, Cost-Optimized) or cpx22 (2 AMD vCPU, 80 GB, Regular Performance), both listed for nbg1 and hel1 (check the Console for the location). The image follows the architecture of the type and the cloud-init handles aarch64 and x86_64."
}

# No `rule` block at all: once a firewall is applied to a server, inbound traffic that matches no rule is blocked,
# and outbound traffic is allowed when there is no outbound rule (docs.hetzner.com/cloud/firewalls/overview). The
# tunnel, the image pulls and the 15-minute desired.env read go out over 443; apt and DNS need more.
resource "hcloud_firewall" "node" {
  name = "s1d1-node-no-inbound"

  labels = {
    project = "s1d1-alliance"
    role    = "node"
  }
}

resource "hcloud_server" "node" {
  name        = "s1d1-node"
  server_type = var.hcloud_server_type
  location    = var.hcloud_location

  # "ubuntu-24.04" exists for x86 and for Arm: the provider picks the one of the server type's architecture.
  image = "ubuntu-24.04"

  # IPv4 as well as IPv6: ghcr.io and github.com are not all reachable over IPv6 only. Both are outbound use only.
  public_net {
    ipv4_enabled = true
    ipv6_enabled = true
  }

  firewall_ids = [hcloud_firewall.node.id]

  # No ssh_keys on purpose. Hetzner then e-mails a root password; the cloud-init disables password logins and SSH.
  user_data = templatefile("${path.module}/../node/cloud-init.yaml.tftpl", {
    tunnel_token    = data.cloudflare_zero_trust_tunnel_cloudflared_token.s1d1.token
    community_token = random_password.community_token.result
    desired_url     = "https://raw.githubusercontent.com/${var.github_repository}/main/node/desired.env"
  })

  labels = {
    project = "s1d1-alliance"
    role    = "node"
  }

  # A newer Ubuntu image or an edited cloud-init must not replace a running node: the node updates itself from
  # desired.env. To rebuild it on purpose: terraform apply -replace=hcloud_server.node
  lifecycle {
    ignore_changes = [user_data, image]
  }
}

output "node_ipv4" {
  description = "Public IPv4 of the node (outbound traffic only; nothing listens)."
  value       = hcloud_server.node.ipv4_address
}
