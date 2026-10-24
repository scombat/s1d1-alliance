# node

A community node: tantive (the s1d1 router) behind a Cloudflare Tunnel. The node opens no inbound port, and it only
ever starts a tantive image whose cosign signature verifies.

| File | Role |
| --- | --- |
| `compose.yaml` | `tantive` (no published port, read-only, capture volume), `capture-init` (gives the volume to uid 10001) and `cloudflared` (`tunnel run`). Runs unchanged on the Hetzner VM and on a homelab. |
| `desired.env` | The images every node runs, by digest. This is the file the nodes read from `main`. |
| `update.sh` | `update.sh <env file> <desired url>`: download `desired.env`, verify, rewrite the images in the env file, `docker compose up -d`. |
| `s1d1-update.service`, `s1d1-update.timer` | Run `update.sh` 2 minutes after boot and every 15 minutes. |
| `cloud-init.yaml.tftpl` | Terraform `templatefile` for the Hetzner VM (variables `tunnel_token`, `community_token`, `desired_url`). Embeds the files above verbatim. |
| `example.env` | Template of `/opt/s1d1/.env`. |
| `test/` | `sh node/test/test_update.sh` (update.sh, with fake `cosign`, `curl`, `docker`) and `sh node/test/test_cloud_init.sh` (the cloud-init copies have not drifted). |

## What update.sh trusts

`update.sh` reads `desired.env` and accepts a closed list of two keys, each bound to its own repository, written with
the explicit registry and pinned by digest:

- `TANTIVE_IMAGE=ghcr.io/scombat/s1d1-tantive:<tag>@sha256:<64 hex>`
- `CLOUDFLARED_IMAGE=docker.io/cloudflare/cloudflared:<tag>@sha256:<64 hex>`

Any other key (including any other `*_IMAGE`), another registry or repository, or a reference without a digest refuses
the whole file (exit 1): a compromised `main` cannot make a node run an arbitrary image next to the tunnel token. For
each image that differs from the env file:

- `TANTIVE_IMAGE` is verified **keyless**: `cosign verify <image> --certificate-identity-regexp <the build workflow of
  scombat/s1d1-tantive> --certificate-oidc-issuer https://token.actions.githubusercontent.com`, exactly as
  [s1d1-tantive/docs/verify.md](https://github.com/scombat/s1d1-tantive/blob/main/docs/verify.md) documents for
  releases from 0.6.0 on (recorded in Rekor). There is no key to install or rotate; the project key `cosign.pub` of
  s1d1-tantive only verifies images up to 0.5.0 and this repository does not carry it.
- `CLOUDFLARED_IMAGE` is not signed by us, so it is not cosign-verified. Its repository is fixed above and it changes
  only through a pull request on `node/desired.env`. The set of verified keys is the `VERIFIED_KEYS` variable of the script.

All verifications run before anything is written. The env file is replaced atomically (temporary file in a scratch
folder inside the node folder, so on the same filesystem, then `mv`, mode 0600). Exit codes:

- 0: up to date or updated;
- 1: refused (bad desired file, signature that does not verify) **or** `docker compose up -d` failed. In the second
  case the previous env file is put back, `docker compose up -d` runs once more on it (its result is ignored) and the
  next timer run retries;
- 2: the download failed.

The tokens are never read, printed or logged by the script.

## Dependencies

- `docker.io` and `docker-compose-v2` from Ubuntu's own repositories: the simplest source of `docker compose`, with no
  third-party apt key to trust.
- `cosign` v3.1.4 from its GitHub release, pinned by version, its sha256 checked in cloud-init before installation.
  Needed to verify the signatures; nothing else does it.
- `cloudflare/cloudflared` 2026.10.3, pinned by digest: the tunnel client, the only thing that leaves the node.

## Homelab

Copy `compose.yaml`, `update.sh`, `s1d1-update.service` and `s1d1-update.timer` to `/opt/s1d1`, copy `example.env` to
`/opt/s1d1/.env` (mode 0600) with the two secrets, install cosign, then `systemctl enable --now s1d1-update.timer`.
The tunnel's ingress is `http://tantive:8788`: the service name on the Compose network.

## The `/api` prefix

Nothing rewrites the path between the public name and the node: a request for `https://s1d1.qoo.gy/api/v1/systemone`
reaches tantive as `/api/v1/systemone`. `S1D1_ROOT_PATH=/api` in `compose.yaml` does not by itself make such a path
work; tantive 0.8.0 and later handle a request path that starts with the prefix (fixed in s1d1-tantive PR #5), so a
node needs a 0.8.0 or later image. Smoke test 9.1 of [docs/operations.md](../docs/operations.md) checks
`POST /api/v1/systemone` end to end.
