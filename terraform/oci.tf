# The community node: one Ampere A1 instance (Always Free shape) that runs the generic compose of ../node through
# cloud-init. It has a public IP only to go OUT (tunnel, ghcr.io, raw.githubusercontent.com, apt); nothing can come IN:
# the security list below has no ingress rule, there is no SSH key, and no port is published by the compose.
variable "oci_tenancy_ocid" {
  type        = string
  description = "OCID of the Oracle Cloud tenancy."
}

variable "oci_user_ocid" {
  type        = string
  description = "OCID of the Oracle Cloud user whose API key Terraform uses."
}

variable "oci_fingerprint" {
  type        = string
  description = "Fingerprint of that user's API key."
}

variable "oci_private_key" {
  type        = string
  sensitive   = true
  description = "PEM content of the API private key (not a path)."
}

variable "oci_region" {
  type        = string
  default     = "eu-paris-1"
  description = "Oracle Cloud region (the home region for Always Free resources)."
}

variable "oci_compartment_ocid" {
  type        = string
  description = "OCID of the compartment that holds the network and the instance (the tenancy OCID is the root compartment)."
}

variable "oci_availability_domain_index" {
  type        = number
  default     = 0
  description = "Index (0, 1, 2) of the availability domain in the region's list. A1 capacity runs out often (\"Out of host capacity\"): change it and apply again."

  validation {
    condition     = var.oci_availability_domain_index >= 0 && var.oci_availability_domain_index <= 2 && floor(var.oci_availability_domain_index) == var.oci_availability_domain_index
    error_message = "oci_availability_domain_index must be 0, 1 or 2."
  }
}

data "oci_identity_availability_domains" "this" {
  compartment_id = var.oci_tenancy_ocid
}

# Latest Ubuntu 24.04 image that runs on the A1 shape (aarch64).
data "oci_core_images" "ubuntu" {
  compartment_id           = var.oci_compartment_ocid
  operating_system         = "Canonical Ubuntu"
  operating_system_version = "24.04"
  shape                    = "VM.Standard.A1.Flex"
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"
}

resource "oci_core_vcn" "node" {
  compartment_id = var.oci_compartment_ocid
  display_name   = "s1d1-node"
  cidr_blocks    = ["10.0.0.0/24"]
  dns_label      = "s1d1node"
}

resource "oci_core_internet_gateway" "node" {
  compartment_id = var.oci_compartment_ocid
  vcn_id         = oci_core_vcn.node.id
  display_name   = "s1d1-node"
  enabled        = true
}

resource "oci_core_route_table" "node" {
  compartment_id = var.oci_compartment_ocid
  vcn_id         = oci_core_vcn.node.id
  display_name   = "s1d1-node"

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.node.id
  }
}

# No ingress_security_rules block at all: nothing may enter. Egress is open (the tunnel, the image pulls and the
# 15-minute desired.env read go out over 443; apt and DNS need more).
resource "oci_core_security_list" "node" {
  compartment_id = var.oci_compartment_ocid
  vcn_id         = oci_core_vcn.node.id
  display_name   = "s1d1-node-no-ingress"

  egress_security_rules {
    destination      = "0.0.0.0/0"
    destination_type = "CIDR_BLOCK"
    protocol         = "all"
    stateless        = false
  }
}

# security_list_ids replaces the VCN's default list (which allows SSH and ICMP in): only the list above is attached.
resource "oci_core_subnet" "node" {
  compartment_id             = var.oci_compartment_ocid
  vcn_id                     = oci_core_vcn.node.id
  display_name               = "s1d1-node-public"
  cidr_block                 = "10.0.0.0/24"
  dns_label                  = "node"
  route_table_id             = oci_core_route_table.node.id
  security_list_ids          = [oci_core_security_list.node.id]
  prohibit_public_ip_on_vnic = false
}

resource "oci_core_instance" "node" {
  compartment_id      = var.oci_compartment_ocid
  availability_domain = data.oci_identity_availability_domains.this.availability_domains[var.oci_availability_domain_index].name
  display_name        = "s1d1-node"
  shape               = "VM.Standard.A1.Flex"

  shape_config {
    ocpus         = 2
    memory_in_gbs = 12
  }

  source_details {
    source_type = "image"
    source_id   = data.oci_core_images.ubuntu.images[0].id
  }

  create_vnic_details {
    subnet_id        = oci_core_subnet.node.id
    assign_public_ip = true # outbound only: the security list has no ingress rule
  }

  metadata = {
    user_data = base64encode(templatefile("${path.module}/../node/cloud-init.yaml.tftpl", {
      tunnel_token    = data.cloudflare_zero_trust_tunnel_cloudflared_token.s1d1.token
      community_token = random_password.community_token.result
      desired_url     = "https://raw.githubusercontent.com/${var.github_repository}/main/node/desired.env"
    }))
  }

  # A newer Ubuntu image or an edited cloud-init must not replace a running node: the node updates itself from
  # desired.env. To rebuild it on purpose: terraform apply -replace=oci_core_instance.node
  lifecycle {
    ignore_changes = [metadata, source_details]
  }
}

output "node_public_ip" {
  description = "Public IP of the Oracle node (outbound traffic only; nothing listens)."
  value       = oci_core_instance.node.public_ip
}
