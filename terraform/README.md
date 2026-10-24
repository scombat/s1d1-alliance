# terraform

Infrastructure of the public instance `s1d1.qoo.gy`, in HCP Terraform (workspace `s1d1-alliance`).
This folder holds the Cloudflare part (tunnel, DNS, Workers, routes, rate limit, R2); the Google Cloud part (raw
bucket, service accounts, anonymizer) is `gcp.tf`, added by the next task.

Providers are pinned exactly: `cloudflare/cloudflare = 5.28.1`, `hashicorp/random = 3.9.1`. The lock file
`.terraform.lock.hcl` is committed with the hashes for `linux_amd64` (CI, HCP) and `darwin_arm64` (laptops).
To add a platform or upgrade: `terraform providers lock -platform=linux_amd64 -platform=darwin_arm64`.

## HCP Terraform workspace

`versions.tf` has `cloud { workspaces { name = "s1d1-alliance" } }`. The organisation is not in the code:

```
export TF_CLOUD_ORGANIZATION=<your organisation>
terraform login            # or TF_TOKEN_app_terraform_io
terraform init
```

The workspace runs remotely, so the variables below are set on the workspace (sensitive ones marked as such), not in
a file. `*.tfvars` is ignored by git; never commit one.

Checks that need no account (what CI runs):

```
terraform fmt -check -recursive
terraform init -backend=false     # the `cloud` block does not get in the way
terraform validate
```

## Variables

| name | sensitive | default | role |
|---|---|---|---|
| `cloudflare_account_id` | no | none | account that owns the tunnel, the Workers, R2 |
| `cloudflare_zone_id` | no | none | zone `qoo.gy` |
| `cloudflare_api_token` | yes | none | token of the provider (permissions below) |
| `deploy_version` | no | `v1` | `DEPLOY_VERSION`, part of the cache key: bump it to empty the cache |
| `status_commit` | no | `unknown` | `STATUS_COMMIT` of `/status.json` |
| `tantive_image` | no | `unknown` | `STATUS_TANTIVE_IMAGE` of `/status.json` |
| `status_model` | no | `unknown` | `STATUS_MODEL` of `/status.json` |
| `maintenance` | no | `0` | `"1"`: `/api/*` and `/ingest` answer 503, the landing stays up |
| `cache_ttl` | no | `86400` | `CACHE_TTL` in seconds |

The GCS values of the ingest Worker (`RAW_BUCKET`, `GCS_HMAC_ID`, `GCS_HMAC_SECRET`) are not variables: they come
from the raw bucket and the HMAC key that `gcp.tf` creates. Until it exists, `gcs_hmac_locals.tf` holds three empty
placeholders (`local.raw_bucket_name`, `local.gcs_hmac_id`, `local.gcs_hmac_secret`); `gcp.tf` defines them and
that file is deleted. **Do not apply before that**: an empty secret binding is refused.

## Outputs (all sensitive except `r2_endpoint`)

| name | what |
|---|---|
| `tunnel_token` | for `cloudflared tunnel run --token` on the nodes |
| `community_token` | `S1D1_CAPTURE_TOKEN` of the community node, `COMMUNITY_TOKEN` of the ingest Worker |
| `r2_access_key_id` | S3 access key id of `s1d1-clean`: the id of the API token |
| `r2_secret_access_key` | S3 secret: the SHA-256 of the API token value |
| `r2_endpoint` | `https://<account>.eu.r2.cloudflarestorage.com` (EU jurisdiction: a jurisdictional bucket is only reachable on its own endpoint) |

Read one with `terraform output -raw community_token` (or in the workspace page).

## What is created

- Tunnel `s1d1` (remotely managed) and its ingress: `s1d1.qoo.gy`, path
  `^/api/(v1/(chat/completions|models|decisions|systemone(/batch)?)|classify|predict|models)$` to
  `http://tantive:8788`, then `http_status:404`. No path is rewritten (tantive runs with `S1D1_ROOT_PATH=/api`).
- `CNAME s1d1` to `<tunnel id>.cfargotunnel.com`, proxied.
- Workers `s1d1-site` (static files of `../site`, Analytics Engine binding `VISITS` on dataset `s1d1_visits`) and
  `s1d1-ingest`; routes `s1d1.qoo.gy/*` to the first and `s1d1.qoo.gy/ingest` to the second.
- Ruleset `http_ratelimit`: 100 requests per 10 s per IP on `starts_with(http.request.uri.path, "/api/")`, action
  `block`, blocked for 10 s.
- R2 bucket `s1d1-clean` (jurisdiction `eu`) and an API token limited to it (object read and write).
- `random_password.community_token` (48 characters, no special character).
- `cloudflare_workers_script_subdomain` with `enabled = false` for both Workers, so that they are served by the
  routes only (a workers.dev copy of the site Worker would not go through the zone's rate limit).

How the Workers are uploaded: no build and no bundler. `content_file` is the main module (`src/index.js`) and the
other modules are `files` entries named by their path (`src/api.js`, `vendor/aws4fetch.js`, ...), so the relative
imports resolve inside the upload as they do on disk. The static assets are uploaded from `../site` by the
provider (`assets.directory`); `run_worker_first = ["/", "/status.json", "/api/*"]`.

## Token permissions (`cloudflare_api_token`)

Create the provider token in the dashboard with the permissions that the provider documents for each resource:

- Account: Workers Scripts Write, Workers R2 Storage Write, Cloudflare Tunnel Write (Connectors), and API Tokens
  Write (the R2 token is created by Terraform; a token cannot grant more than its creator holds, and it needs to
  read the permission group list).
- Zone `qoo.gy`: Workers Routes Write, DNS Write, and the zone ruleset (Zone WAF / rate limiting) write permission.

## Manual steps (Terraform cannot do them)

1. **Route failure mode (fail open / fail closed).** `cloudflare_workers_route` (5.28.1) has only `zone_id`,
   `pattern` and `script`. The setting exists only in the dashboard: Workers & Pages, the Worker, Settings, Domains
   & Routes, the route, "Failure mode". Fail open bypasses the Worker when it cannot run, so `/api/*` would go
   straight to the tunnel without the allow-list, the cache and the maintenance switch; fail closed returns a
   Cloudflare 1027 page. For `s1d1.qoo.gy/*` and `s1d1.qoo.gy/ingest`, choose **fail closed**, and check the
   value after each apply (open question 4 of docs/26).
2. **Tunnel connector.** The tunnel exists after the apply, with no connector: the nodes start `cloudflared` with
   `terraform output -raw tunnel_token`.
3. **GCP wiring.** Task 10: `gcp.tf` replaces `gcs_hmac_locals.tf`.
4. **Account.** R2 must be enabled on the account before the first apply.

## Cloudflare behaviours that matter here

- **The Worker's `ORIGIN` is `https://s1d1.qoo.gy` and it does not loop.** Cloudflare: "Routes cannot be the target
  of a same-zone `fetch()` call", and `fetch()` from a route Worker goes to the application server defined in the
  zone's DNS (here the tunnel CNAME), see https://developers.cloudflare.com/workers/configuration/routing/routes/.
  This holds for **routes**; a Custom Domain on the same hostname would invoke the Worker again, so never add one.
- **Rate limit and same-zone subrequests.** Cloudflare may count a Worker's same-zone subrequest as a second
  request (https://developers.cloudflare.com/waf/rate-limiting-rules/troubleshooting). Its remedy adds
  `cf.worker.upstream_zone` to the expression; the Free plan restricts the rule's fields, so the expression is kept
  as specified. Watch the first days of traffic: if legitimate callers are blocked sooner than 100 requests per
  10 s, that is why.
- **Free plan.** One rate limit rule, period 10 s, mitigation 10 s, count per IP (and per data centre, `cf.colo.id`
  is implicit).
