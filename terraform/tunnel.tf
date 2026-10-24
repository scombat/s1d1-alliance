# The tunnel `s1d1` is remotely managed (config_src = "cloudflare"): its ingress rules live here, the nodes only run
# `cloudflared tunnel run --token <tunnel_token>`.
resource "random_id" "tunnel_secret" {
  byte_length = 32
}

resource "cloudflare_zero_trust_tunnel_cloudflared" "s1d1" {
  account_id    = var.cloudflare_account_id
  name          = "s1d1"
  config_src    = "cloudflare"
  tunnel_secret = random_id.tunnel_secret.b64_std
}

resource "cloudflare_zero_trust_tunnel_cloudflared_config" "s1d1" {
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.s1d1.id

  config = {
    ingress = [
      {
        # The closed list of API paths (the Worker enforces the same list; the tunnel is the second wall).
        # S1D1_ROOT_PATH=/api on tantive: no path is rewritten.
        hostname = local.hostname
        path     = "^/api/(v1/(chat/completions|models|decisions|systemone(/batch)?)|classify|predict|models)$"
        service  = "http://tantive:8788"
      },
      {
        service = "http_status:404"
      },
    ]
  }
}

data "cloudflare_zero_trust_tunnel_cloudflared_token" "s1d1" {
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.s1d1.id
}
